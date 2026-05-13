"""
Air Quality (Airparif) Bronze Ingestion
========================================
Source  : Airparif Open Data – OGC Search API
Endpoint: https://data-airparif-asso.opendata.arcgis.com/api/search/v1/

Fetches current air quality measurements (NO2, PM2.5, O3, etc.) from Airparif
monitoring stations in Paris via the OGC API - Records interface.

Bronze schema
-----------------------
station_id          str      unique station identifier
station_name        str      name / location of station
pollutant           str      e.g. "NO2", "PM2.5", "O3"
value               float    concentration (µg/m³)
unit                str      measurement unit
datetime_utc        datetime measurement timestamp (ISO 8601)
latitude            float    WGS84
longitude           float    WGS84
arrondissement      int      1–20 (derived via point-in-polygon with boundaries)
ingested_at         datetime UTC ingestion timestamp

NEXT STEPS:
  1. Test the endpoint to find the collection ID for air quality data:
     curl https://data-airparif-asso.opendata.arcgis.com/api/search/v1/collections
  2. Replace AIRPARIF_COLLECTION_ID below with the actual ID
  3. Optionally: implement spatial join with boundaries to derive arrondissement
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .base import build_session, get_logger, save_parquet

BASE_URL = "https://data-airparif-asso.opendata.arcgis.com/api/search/v1"
LOG_DIR = Path(__file__).parents[2] / "logs"

# OGC Search API collection ID (contains Feature Layers, CSVs, etc.)
AIRPARIF_COLLECTION_ID = "dataset"
PAGE_SIZE = 1000

# Optional: filter datasets by keyword (e.g., "air quality", "polluant")
# Leave empty to get all datasets
SEARCH_KEYWORD = "air"

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


def _fetch_page(
    session: Any,
    logger: logging.Logger,
    offset: int = 0,
) -> list[dict]:
    """Fetch one page of Airparif items via OGC Search API."""
    url = f"{BASE_URL}/collections/{AIRPARIF_COLLECTION_ID}/items"
    params = {
        "limit": PAGE_SIZE,
        "offset": offset,
        "f": "json",
    }
    if SEARCH_KEYWORD:
        params["q"] = SEARCH_KEYWORD
    resp = session.get(url, params=params)

    if resp.status_code != 200:
        logger.warning(
            "Airparif offset=%d → HTTP %d: %s",
            offset, resp.status_code, resp.text[:200],
        )
        return []

    payload = resp.json()
    return payload.get("features", [])


def _feature_to_row(
    feature: dict,
    ingested_at: datetime,
) -> dict | None:
    """
    Flatten an OGC feature into a Bronze row.
    Returns None if required fields are missing.
    """
    props = feature.get("properties", {})
    geom = feature.get("geometry") or {}
    coords = geom.get("coordinates") if geom.get("type") == "Point" else None

    # Validate required fields
    if not coords or len(coords) < 2:
        return None
    if not props.get("pollutant") or props.get("value") is None:
        return None

    try:
        value = float(props.get("value"))
    except (ValueError, TypeError):
        return None

    return {
        "station_id": props.get("station_id"),
        "station_name": props.get("station_name"),
        "pollutant": props.get("pollutant"),
        "value": value,
        "unit": props.get("unit", "µg/m³"),
        "datetime_utc": props.get("datetime_utc"),
        "latitude": coords[1],
        "longitude": coords[0],
        "arrondissement": None,  # TODO: derive via point-in-polygon with boundaries
        "ingested_at": ingested_at,
    }


def ingest() -> pd.DataFrame:
    """
    Ingest Airparif air quality measurements for Paris.

    Returns
    -------
    pd.DataFrame
        Combined DataFrame (also written to Parquet by date).
    """
    logger = get_logger("air_quality", LOG_DIR)
    ingested_at = datetime.now(timezone.utc)
    session = build_session()

    logger.info("Airparif ingestion started")

    all_rows: list[dict] = []
    offset = 0

    while True:
        logger.info("  Fetching offset=%d", offset)
        features = _fetch_page(session, logger, offset)

        if not features:
            logger.info("  No more features (reached end of dataset)")
            break

        for feat in features:
            if row := _feature_to_row(feat, ingested_at):
                all_rows.append(row)

        logger.debug("    Fetched %d features", len(features))
        if len(features) < PAGE_SIZE:
            break

        offset += PAGE_SIZE

    if not all_rows:
        logger.warning("Airparif ingestion produced no rows.")
        return pd.DataFrame(columns=BRONZE_COLUMNS)

    df = pd.DataFrame(all_rows)[BRONZE_COLUMNS]
    df["datetime_utc"] = pd.to_datetime(df["datetime_utc"], errors="coerce")

    # Partition by date
    run_date = ingested_at.date().isoformat()
    path = save_parquet(
        df,
        source="air_quality",
        partition_col="date",
        partition_value=run_date,
        filename="part-0.parquet",
    )
    logger.info("Airparif ingestion complete — %d rows → %s", len(df), path)
    return df
