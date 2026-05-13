"""
INSEE Local Income (Revenus) Bronze Ingestion — STUB
=====================================================
Source  : INSEE – Filosofi / Revenus localisés des ménages
API     : https://api.insee.fr/catalogue/site/themes/bdd/page/donnees-locales/

TODO:
  1. Obtain an INSEE API key from https://api.insee.fr (free registration).
  2. Implement pagination over IRIS codes within Paris (75XXX).
  3. Normalise to the Bronze schema below.

Bronze schema (planned)
-----------------------
iris_code           str      INSEE IRIS identifier
commune_code        str
arrondissement      int
median_income       float    Median household income (€/UC)
gini_coefficient    float
poverty_rate        float    % households below poverty threshold
year                int
latitude            float    centroid of IRIS polygon
longitude           float
ingested_at         datetime
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from .base import get_logger, save_parquet

LOG_DIR = Path(__file__).parents[2] / "logs"

BRONZE_COLUMNS = [
    "iris_code",
    "commune_code",
    "arrondissement",
    "median_income",
    "gini_coefficient",
    "poverty_rate",
    "year",
    "latitude",
    "longitude",
    "ingested_at",
]


def ingest(year: int = 2021) -> pd.DataFrame:
    """Ingest INSEE income data for Paris IRIS zones. (Not yet implemented)"""
    logger = get_logger("revenus", LOG_DIR)
    logger.warning(
        "revenus.ingest() is a stub — INSEE API key and query logic not yet implemented."
    )
    return pd.DataFrame(columns=BRONZE_COLUMNS)
