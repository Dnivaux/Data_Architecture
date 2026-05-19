"""
Crime (SSMSI) Bronze Ingestion
================================
Source  : SSMSI – Service Statistique Ministériel de la Sécurité Intérieure
Catalog : https://www.data.gouv.fr/api/2/datasets/621df2954fa5a3b5a023e23c/
File    : Base statistique communale (format Parquet, géographie 2025)

The dataset is published in long format — one row per commune × year × indicator
category — covering 2016 to the most recent available year.  No pivot/melt needed.

Discovery strategy
------------------
1. Query data.gouv.fr API v2 for the dataset resources.
2. Select the first resource whose format is 'parquet' and whose title starts
   with 'COM' (communal base, as opposed to DEP or REG).
3. Fall back to a hardcoded URL if the API is unreachable.

Bronze schema
-------------
commune_code    str      INSEE code (e.g. '75112')
arrondissement  int      1–20
crime_category  str      SSMSI indicator label
crime_count     int      Number of recorded offences (nombre)
rate_per_1000   float    Rate per 1 000 inhabitants
year            int      Reference year
ingested_at     datetime UTC ingestion timestamp
"""
from __future__ import annotations

import io
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .base import build_session, get_logger, save_parquet

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DATAGOUV_DATASET_ID = "621df2954fa5a3b5a023e23c"
DATAGOUV_API = f"https://www.data.gouv.fr/api/1/datasets/{DATAGOUV_DATASET_ID}/"

# Fallback URL (communal Parquet, géographie 2025, produit 2026-02-03)
FALLBACK_PARQUET_URL = (
    "https://static.data.gouv.fr/resources/"
    "bases-statistiques-communale-departementale-et-regionale-de-la-delinquance"
    "-enregistree-par-la-police-et-la-gendarmerie-nationales/"
    "20260326-124228/"
    "donnee-comm-data.gouv-parquet-2025-geographie2025-produit-le2026-02-03.parquet"
)

LOG_DIR = Path(__file__).parents[2] / "logs"

BRONZE_COLUMNS = [
    "commune_code",
    "arrondissement",
    "crime_category",
    "crime_count",
    "rate_per_1000",
    "year",
    "ingested_at",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _discover_parquet_url(session: object, logger: object) -> str:
    """
    Query data.gouv.fr API v1 to find the latest communal Parquet resource.
    Returns the URL, or FALLBACK_PARQUET_URL on any error.
    """
    try:
        resp = session.get(DATAGOUV_API)
        if resp.status_code != 200:
            logger.warning(
                "data.gouv.fr API returned HTTP %d — using fallback URL",
                resp.status_code,
            )
            return FALLBACK_PARQUET_URL

        resources = resp.json().get("resources", [])
        for res in resources:
            if (
                res.get("format", "").lower() == "parquet"
                and res.get("title", "").upper().startswith("COM")
            ):
                url = res["url"]
                logger.info("Discovered communal Parquet resource: %s", url)
                return url

        logger.warning("No matching Parquet resource found — using fallback URL")
    except Exception as exc:
        logger.warning("Discovery request failed (%s) — using fallback URL", exc)

    return FALLBACK_PARQUET_URL


def _download_parquet(session: object, logger: object, url: str) -> pd.DataFrame:
    """Download a Parquet file in-memory and return a DataFrame."""
    logger.info("Downloading Parquet from %s", url)
    resp = session.get(url)
    if resp.status_code != 200:
        logger.error("HTTP %d when downloading Parquet: %s", resp.status_code, url)
        return pd.DataFrame()

    size_kb = len(resp.content) // 1024
    logger.info("Downloaded %d KB — parsing Parquet", size_kb)
    return pd.read_parquet(io.BytesIO(resp.content), engine="pyarrow")


# ---------------------------------------------------------------------------
# Public entry-point
# ---------------------------------------------------------------------------

def ingest() -> pd.DataFrame:
    """
    Ingest SSMSI communal crime statistics for the 20 Paris arrondissements.

    All available years (2016–latest) are included in the output so downstream
    Silver/Gold layers can freely slice by year.

    Returns
    -------
    pd.DataFrame
        Normalised Bronze DataFrame (also written to Parquet).
    """
    logger = get_logger("crime", LOG_DIR)
    ingested_at = datetime.now(timezone.utc)

    logger.info("Crime ingestion started (SSMSI communal base)")
    session = build_session(retries=3, backoff_factor=1.0, timeout=90)

    # 1. Discover URL
    parquet_url = _discover_parquet_url(session, logger)

    # 2. Download
    df_raw = _download_parquet(session, logger, parquet_url)
    if df_raw.empty:
        logger.error("Empty or failed download — aborting.")
        return pd.DataFrame(columns=BRONZE_COLUMNS)

    logger.info("Raw shape: %d rows × %d cols", *df_raw.shape)

    # 3. Filter Paris arrondissements (CODGEO_2025 in 75101–75120)
    code_col = "CODGEO_2025"
    df_paris = df_raw[
        df_raw[code_col].astype(str).str.match(r"^751\d{2}$")
    ].copy()
    logger.info(
        "Filtered to Paris: %d rows (%d arrondissements, %d years, %d indicators)",
        len(df_paris),
        df_paris[code_col].nunique(),
        df_paris["annee"].nunique(),
        df_paris["indicateur"].nunique(),
    )

    # 4. Normalise to Bronze schema
    df_paris["arrondissement"] = df_paris[code_col].astype(str).str[-2:].astype(int)

    df = pd.DataFrame({
        "commune_code":    df_paris[code_col].astype(str),
        "arrondissement":  df_paris["arrondissement"].astype(int),
        "crime_category":  df_paris["indicateur"].astype(str),
        "crime_count":     pd.to_numeric(df_paris["nombre"],         errors="coerce"),
        "rate_per_1000":   pd.to_numeric(df_paris["taux_pour_mille"], errors="coerce"),
        "year":            df_paris["annee"].astype(int),
        "ingested_at":     ingested_at,
    })

    # Rows with null crime_count are suppressed by SSMSI for confidentiality
    null_count = df["crime_count"].isna().sum()
    if null_count:
        logger.warning(
            "  %d rows with null crime_count (SSMSI confidentiality suppression) — kept as-is",
            null_count,
        )

    df["crime_count"] = df["crime_count"].astype("Int64")
    df = df[BRONZE_COLUMNS].sort_values(
        ["arrondissement", "year", "crime_category"]
    ).reset_index(drop=True)

    # 5. Persist
    path = save_parquet(df, source="crime", filename="part-0.parquet")
    logger.info("Crime ingestion complete — %d rows → %s", len(df), path)

    return df


if __name__ == "__main__":
    result = ingest()
    if not result.empty:
        print("\n--- Aperçu du DataFrame Bronze (crime) ---")
        print(result.head(15).to_string(index=False))
        print(f"\nShape  : {result.shape}")
        print(f"Années : {sorted(result['year'].unique())}")
        print(f"Catégories ({result['crime_category'].nunique()}) :")
        for c in sorted(result['crime_category'].unique()):
            print(f"  - {c}")
        print(f"\nTypes  :\n{result.dtypes}")
