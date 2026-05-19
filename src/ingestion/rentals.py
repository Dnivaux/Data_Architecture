"""
Rentals Bronze Ingestion
========================
Source  : Koumoul / Data Fair – Indicateurs de loyers d'annonce par commune
Dataset : Indicateurs de loyers d'annonce par commune - appartements - 2025
ID      : qgkdrq4knk147b7no6jnktvl
Endpoint: https://opendata.koumoul.com/data-fair/api/v1/datasets/<id>/lines

Fetches predicted median rent per m² (loypredm2) for the 20 Paris
arrondissements (INSEE 75101–75120, dep=75) and persists a single
Parquet file in the Bronze layer.

Bronze schema
-------------
code_commune    str      INSEE commune code         e.g. '75107'
arrondissement  int      Derived from code          e.g. 7
loyer_m2        float    Predicted median rent €/m²
loyer_m2_bas    float    Lower confidence bound €/m²
loyer_m2_haut   float    Upper confidence bound €/m²
nb_obs          int      Number of observations used
libgeo          str      Commune label              e.g. 'Paris 7e Arrondissement'
type_bien       str      Always 'Appartement'
ingested_at     datetime UTC ingestion timestamp
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .base import build_session, get_logger, save_parquet

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DATASET_ID = "qgkdrq4knk147b7no6jnktvl"
BASE_URL = (
    f"https://opendata.koumoul.com/data-fair/api/v1/datasets/{DATASET_ID}/lines"
)
PAGE_SIZE = 100   # dataset has 20 Paris rows; kept for safety

LOG_DIR = Path(__file__).parents[2] / "logs"

BRONZE_COLUMNS = [
    "code_commune",
    "arrondissement",
    "loyer_m2",
    "loyer_m2_bas",
    "loyer_m2_haut",
    "nb_obs",
    "libgeo",
    "type_bien",
    "ingested_at",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (ValueError, TypeError):
        return None


def _to_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (ValueError, TypeError):
        return None


def _row_to_record(row: dict, ingested_at: datetime) -> dict:
    """Map one API result dict to a Bronze record."""
    insee = str(row.get("insee_c", ""))
    arrond_suffix = insee[-2:] if len(insee) == 5 else None
    return {
        "code_commune":    insee,
        "arrondissement":  _to_int(arrond_suffix),
        "loyer_m2":        _to_float(row.get("loypredm2")),
        "loyer_m2_bas":    _to_float(row.get("lwripm2")),
        "loyer_m2_haut":   _to_float(row.get("upripm2")),
        "nb_obs":          _to_int(row.get("nbobs_com")),
        "libgeo":          row.get("libgeo"),
        "type_bien":       "Appartement",
        "ingested_at":     ingested_at,
    }


def _fetch_all(session: Any, logger: Any, params: dict) -> list[dict]:
    """
    Paginate through the Data Fair /lines endpoint until exhausted.
    Returns the flat list of result dicts.
    """
    results: list[dict] = []
    offset = 0

    while True:
        page_params = {**params, "size": PAGE_SIZE, "after": offset} if offset else {**params, "size": PAGE_SIZE}
        resp = session.get(BASE_URL, params=page_params)

        if resp.status_code != 200:
            logger.warning(
                "Koumoul API offset=%d → HTTP %d: %s",
                offset, resp.status_code, resp.text[:200],
            )
            break

        payload = resp.json()
        batch = payload.get("results", [])
        results.extend(batch)
        logger.debug("  offset=%d → %d records (total_seen=%d)", offset, len(batch), len(results))

        total = payload.get("total", 0)
        if len(results) >= total or len(batch) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    return results


# ---------------------------------------------------------------------------
# Public entry-point
# ---------------------------------------------------------------------------

def ingest() -> pd.DataFrame:
    """
    Ingest apartment rental indicators for the 20 Paris arrondissements.

    Returns
    -------
    pd.DataFrame
        Normalised Bronze DataFrame (also written to Parquet).
    """
    logger = get_logger("rentals", LOG_DIR)
    ingested_at = datetime.now(timezone.utc)

    logger.info(
        "Rentals ingestion started — dataset=%s (appartements 2025)",
        DATASET_ID,
    )

    session = build_session(retries=3, backoff_factor=0.5)

    # dep:75 matches all Paris arrondissements (INSEE 75101–75120)
    raw_rows = _fetch_all(session, logger, params={"qs": "dep:75"})

    if not raw_rows:
        logger.warning("Rentals ingestion produced no rows.")
        return pd.DataFrame(columns=BRONZE_COLUMNS)

    logger.info("Fetched %d raw rows — building DataFrame", len(raw_rows))

    records = [_row_to_record(row, ingested_at) for row in raw_rows]
    df = pd.DataFrame(records)[BRONZE_COLUMNS]

    # Type coercions
    df["loyer_m2"]      = pd.to_numeric(df["loyer_m2"],      errors="coerce")
    df["loyer_m2_bas"]  = pd.to_numeric(df["loyer_m2_bas"],  errors="coerce")
    df["loyer_m2_haut"] = pd.to_numeric(df["loyer_m2_haut"], errors="coerce")
    df["arrondissement"]= pd.to_numeric(df["arrondissement"], errors="coerce").astype("Int64")
    df["nb_obs"]        = pd.to_numeric(df["nb_obs"],         errors="coerce").astype("Int64")

    # Validation: keep only genuine Paris arrondissements
    invalid = ~df["arrondissement"].between(1, 20)
    if invalid.any():
        logger.warning("  Dropping %d rows with arrondissement outside 1–20", invalid.sum())
        df = df[~invalid].copy()

    null_loyer = df["loyer_m2"].isna().sum()
    if null_loyer:
        logger.warning("  %d rows with null loyer_m2", null_loyer)

    df = df.sort_values("arrondissement").reset_index(drop=True)

    path = save_parquet(df, source="rentals", filename="part-0.parquet")
    logger.info(
        "Rentals ingestion complete — %d arrondissements — %s",
        len(df), path,
    )
    return df


if __name__ == "__main__":
    result = ingest()
    if not result.empty:
        print("\n--- DataFrame Bronze (rentals) ---")
        print(result.to_string(index=False))
        print(f"\nShape  : {result.shape}")
        print(f"\nTypes  :\n{result.dtypes}")
