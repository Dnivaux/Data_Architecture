"""
Crime (SSMSI) Bronze Ingestion — STUB
======================================
Source  : SSMSI – Service Statistique Ministériel de la Sécurité Intérieure
Data    : https://www.data.gouv.fr/fr/datasets/bases-statistiques-communales-departementales-et-regionales-de-la-delinquance-enregistree-par-la-police-et-la-gendarmerie-nationales/

NOTE: SSMSI does not expose a live REST API — data is distributed as annual CSV/XLSX
files on data.gouv.fr.  The endpoint in the brief referencing Overpass appears to be
a mismatch; this module will download and parse the official SSMSI CSV release.

TODO:
  1. Download the latest "base-statistique-communale" CSV from data.gouv.fr.
  2. Filter for dept=75 (Paris), join with arrondissement boundaries.
  3. Normalise to the Bronze schema below.

Bronze schema (planned)
-----------------------
commune_code        str      INSEE commune code (e.g. "75056" for Paris)
arrondissement      int      1–20 (derived)
crime_category      str      SSMSI libellé
crime_count         int
year                int
rate_per_1000       float    per 1 000 inhabitants
latitude            float    arrondissement centroid
longitude           float
ingested_at         datetime
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from .base import get_logger

LOG_DIR = Path(__file__).parents[2] / "logs"

BRONZE_COLUMNS = [
    "commune_code",
    "arrondissement",
    "crime_category",
    "crime_count",
    "year",
    "rate_per_1000",
    "latitude",
    "longitude",
    "ingested_at",
]


def ingest(year: int = 2023) -> pd.DataFrame:
    """Ingest SSMSI crime statistics for Paris. (Not yet implemented)"""
    logger = get_logger("crime", LOG_DIR)
    logger.warning("crime.ingest() is a stub — SSMSI CSV download not yet implemented.")
    return pd.DataFrame(columns=BRONZE_COLUMNS)
