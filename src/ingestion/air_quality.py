"""
Airparif Air Quality (Indice ATMO) Bronze Ingestion
====================================================
Source  : Airparif API  –  /indices/prevision/commune
API doc : https://api.airparif.asso.fr/docs

Flow
----
1. Bulk GET for the 20 Paris INSEE codes (75101–75120) using repeated
   `insee` query params (the API rejects comma-separated values).
2. Flatten the nested JSON into one row per commune × forecast day.
3. Map qualitative ATMO labels to the official 1-6 numerical scale.
4. Persist to Parquet, partitioned by ingestion run-date.

Bronze schema
-------------
commune_code    str      INSEE code            e.g. '75114'
arrondissement  int      Derived from code     e.g. 14
date_prevision  date     Forecast date
indice_atmo     str      Overall ATMO label    e.g. 'Moyen'
indice_atmo_num int      Numerical ATMO 1-6
no2             str      Per-pollutant label
o3              str
pm10            str
pm25            str
so2             str
ingested_at     datetime UTC ingestion timestamp
"""
from __future__ import annotations

import os
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from .base import build_session, get_logger, save_parquet

# Load .env when running locally (no-op if already set by the environment)
load_dotenv(Path(__file__).parents[2] / ".env")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

ENDPOINT = "https://api.airparif.fr/indices/prevision/commune"

PARIS_INSEE_CODES = [f"751{str(i).zfill(2)}" for i in range(1, 21)]

LOG_DIR = Path(__file__).parents[2] / "logs"

# Official ATMO qualitative → numerical scale (France, revision 2020)
ATMO_SCALE: dict[str, int] = {
    "bon":                   1,
    "moyen":                 2,
    "dégradé":               3,
    "degrade":               3,
    "mauvais":               4,
    "très mauvais":          5,
    "tres mauvais":          5,
    "extrêmement mauvais":   6,
    "extremement mauvais":   6,
}

BRONZE_COLUMNS = [
    "commune_code",
    "arrondissement",
    "date_prevision",
    "indice_atmo",
    "indice_atmo_num",
    "no2",
    "o3",
    "pm10",
    "pm25",
    "so2",
    "ingested_at",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _atmo_to_int(label: str | None) -> int | None:
    if not label:
        return None
    return ATMO_SCALE.get(label.strip().lower())


def _prev_to_row(insee: str, prev: dict, ingested_at: datetime) -> dict:
    """Flatten one forecast dict into a Bronze row."""
    return {
        "commune_code":    insee,
        "arrondissement":  int(insee[-2:]),
        "date_prevision":  prev.get("date"),
        "indice_atmo":     prev.get("indice"),
        "indice_atmo_num": _atmo_to_int(prev.get("indice")),
        "no2":             prev.get("no2"),
        "o3":              prev.get("o3"),
        "pm10":            prev.get("pm10"),
        "pm25":            prev.get("pm25"),
        "so2":             prev.get("so2"),
        "ingested_at":     ingested_at,
    }


# ---------------------------------------------------------------------------
# Public entry-point
# ---------------------------------------------------------------------------

def ingest() -> pd.DataFrame:
    """Ingest Airparif ATMO forecasts for the 20 Paris arrondissements."""
    logger = get_logger("air_quality", LOG_DIR)
    ingested_at = datetime.now(timezone.utc)
    run_date = date.today().isoformat()

    # 1. API key – fail fast if missing
    api_key = os.environ.get("AIRPARIF_API_KEY")
    if not api_key:
        logger.error(
            "AIRPARIF_API_KEY is not set. "
            "Add it to your .env file or export it as an environment variable."
        )
        return pd.DataFrame(columns=BRONZE_COLUMNS)
    headers = {"X-Api-Key": api_key, "Accept": "application/json"}

    # 2. Bulk request — one param per code (API rejects CSV)
    session = build_session(retries=3, backoff_factor=0.5)
    params = [("insee", code) for code in PARIS_INSEE_CODES]

    logger.info(
        "Airparif ingestion started — fetching %d communes (run-date=%s)",
        len(PARIS_INSEE_CODES), run_date,
    )
    resp = session.get(ENDPOINT, headers=headers, params=params)

    if resp.status_code != 200:
        logger.error(
            "Airparif API returned HTTP %d: %s", resp.status_code, resp.text[:300]
        )
        return pd.DataFrame(columns=BRONZE_COLUMNS)

    try:
        data = resp.json()
    except ValueError as exc:
        logger.error("Failed to parse JSON response: %s", exc)
        return pd.DataFrame(columns=BRONZE_COLUMNS)

    # 3. Flatten JSON  {insee: [{date, indice, no2, o3, ...}, ...], ...}
    logger.info("Flattening response — %d commune entries received", len(data))
    records: list[dict] = []
    for insee, previsions in data.items():
        if not isinstance(previsions, list):
            logger.warning("  Unexpected payload for commune %s — skipped", insee)
            continue
        for prev in previsions:
            records.append(_prev_to_row(insee, prev, ingested_at))

    if not records:
        logger.warning("Airparif ingestion produced no rows.")
        return pd.DataFrame(columns=BRONZE_COLUMNS)

    # 4. Normalise types
    df = pd.DataFrame(records)[BRONZE_COLUMNS]
    df["date_prevision"] = pd.to_datetime(df["date_prevision"], errors="coerce").dt.date
    df["indice_atmo_num"] = pd.to_numeric(df["indice_atmo_num"], errors="coerce").astype("Int64")

    missing_num = df["indice_atmo_num"].isna().sum()
    if missing_num:
        logger.warning(
            "  %d rows with unmapped indice_atmo_num (unknown label?): %s",
            missing_num,
            df.loc[df["indice_atmo_num"].isna(), "indice_atmo"].unique().tolist(),
        )

    # 5. Persist
    path = save_parquet(
        df,
        source="air_quality",
        partition_col="date",
        partition_value=run_date,
        filename="part-0.parquet",
    )
    logger.info(
        "Airparif ingestion complete — %d rows → %s", len(df), path
    )
    return df


if __name__ == "__main__":
    result = ingest()
    if not result.empty:
        print("\n--- Aperçu du DataFrame Bronze ---")
        print(result.to_string(index=False))
        print(f"\nShape : {result.shape}")
        print(f"\nTypes :\n{result.dtypes}")
