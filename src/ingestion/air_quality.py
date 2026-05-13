"""
Air Quality (Airparif) Bronze Ingestion — STUB
===============================================
Source  : Airparif Open Data – ArcGIS REST API
Endpoint: https://data-airparif-asso.opendata.arcgis.com/

TODO:
  1. Identify the target Feature Layer URL (e.g. NO2 / PM2.5 hourly measurements).
  2. Query via the ArcGIS FeatureServer endpoint with `f=geojson&outFields=*`.
  3. Implement time-range pagination (resultOffset / resultRecordCount).
  4. Normalise to the Bronze schema below.

Bronze schema (planned)
-----------------------
station_id          str
station_name        str
pollutant           str      e.g. "NO2", "PM2.5", "O3"
value               float    µg/m³
unit                str
datetime_utc        datetime
latitude            float
longitude           float
arrondissement      int      derived via point-in-polygon with boundaries
ingested_at         datetime
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from .base import get_logger

LOG_DIR = Path(__file__).parents[2] / "logs"

BRONZE_COLUMNS = [
    "station_id",
    "station_name",
    "pollutant",
    "value",
    "unit",
    "datetime_utc",
    "latitude",
    "longitude",
    "arrondissement",
    "ingested_at",
]


def ingest() -> pd.DataFrame:
    """Ingest Airparif air quality measurements. (Not yet implemented)"""
    logger = get_logger("air_quality", LOG_DIR)
    logger.warning("air_quality.ingest() is a stub — Airparif ArcGIS layer not yet wired up.")
    return pd.DataFrame(columns=BRONZE_COLUMNS)
