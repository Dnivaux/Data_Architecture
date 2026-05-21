"""
OSM / Overpass Bronze Ingestion
================================
Source  : OpenStreetMap via Overpass API
Endpoints: https://overpass.private.coffee/api/interpreter (primary)
           https://overpass-api.de/api/interpreter (fallback)

Fetches amenity points-of-interest inside Paris for selected categories,
including bars, cinemas, stadiums, schools, shops, and restaurants.

Both OSM *nodes* (direct lat/lon) and *ways* (polygon centroid via `out center`)
are normalised to a single point geometry.

Bronze schema
-------------
osm_id          int      OSM element id
osm_type        str      "node" | "way" | "relation"
amenity_type    str      "bar" | "nightclub" | "park"
name            str      official name tag (nullable)
latitude        float
longitude       float
opening_hours   str      OSM opening_hours tag (nullable)
wheelchair      str      OSM wheelchair tag (nullable)
tags            str      full JSON-encoded tag dict
ingested_at     datetime UTC timestamp
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    # Allow running as a script: python src/ingestion/osm.py
    repo_root = Path(__file__).parents[2]
    sys.path.insert(0, str(repo_root))
    __package__ = "src.ingestion"

import pandas as pd

from .base import build_session, get_logger, save_parquet

OVERPASS_URLS = [
    os.environ.get("OVERPASS_URL"),
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass-api.de/api/interpreter",
]
OVERPASS_URLS = [url for url in OVERPASS_URLS if url]
LOG_DIR = Path(__file__).parents[2] / "logs"
OSM_HTTP_TIMEOUT = int(os.environ.get("OSM_HTTP_TIMEOUT", "120"))
OSM_QUERY_TIMEOUT = int(os.environ.get("OSM_QUERY_TIMEOUT", "120"))

# Overpass query template – fetches nodes + ways, returns center coords for ways
_QUERY_TEMPLATE = """
[out:json][timeout:{timeout}];
area["name"="Paris"]["admin_level"="8"]->.paris;
(
  node[{tag_filter}](area.paris);
  way[{tag_filter}](area.paris);
);
out center tags;
"""

AMENITY_FILTERS: dict[str, str] = {
    "bar": 'amenity="bar"',
    "cinema": 'amenity="cinema"',
    "college": 'amenity="college"',
    "nightclub": 'amenity="nightclub"',
    "park": 'leisure="park"',
    "restaurant": 'amenity="restaurant"',
    "school": 'amenity="school"',
    "shop": 'shop~"supermarket|hypermarket|convenience|grocery|department_store|mall"',
    "stadium": 'leisure="stadium"',
    "university": 'amenity="university"',
}

BRONZE_COLUMNS = [
    "osm_id",
    "osm_type",
    "amenity_type",
    "name",
    "latitude",
    "longitude",
    "opening_hours",
    "wheelchair",
    "tags",
    "ingested_at",
]


def _build_query(tag_filter: str) -> str:
    return _QUERY_TEMPLATE.format(tag_filter=tag_filter, timeout=OSM_QUERY_TIMEOUT).strip()


def _fetch_amenity(
    session: Any,
    logger: logging.Logger,
    amenity_type: str,
    tag_filter: str,
) -> list[dict]:
    """POST the Overpass query and return raw elements list."""
    query = _build_query(tag_filter)
    logger.debug("Overpass query for '%s':\n%s", amenity_type, query)

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
        "User-Agent": "UrbanDataExplorer/1.0",
    }

    for url in OVERPASS_URLS:
        resp = session.post(url, data={"data": query}, headers=headers)
        if resp.status_code == 406:
            # Some instances accept raw query text instead of form-encoded.
            headers["Content-Type"] = "text/plain; charset=utf-8"
            resp = session.post(url, data=query, headers=headers)

        if resp.status_code != 200:
            logger.warning(
                "OSM %s → HTTP %d (%s): %s",
                amenity_type, resp.status_code, url, resp.text[:300],
            )
            continue

        try:
            data = resp.json()
        except ValueError as exc:
            logger.warning("OSM %s JSON parse error (%s): %s", amenity_type, url, exc)
            continue

        return data.get("elements", [])

    return []


def _element_to_row(
    element: dict,
    amenity_type: str,
    ingested_at: datetime,
) -> dict | None:
    """
    Convert an Overpass element to a Bronze row.
    Ways include a 'center' dict; nodes have top-level lat/lon.
    Returns None if coordinates are unavailable.
    """
    osm_type = element.get("type")  # "node" | "way"
    tags: dict = element.get("tags", {})

    if osm_type == "node":
        lat = element.get("lat")
        lon = element.get("lon")
    elif osm_type == "way":
        center = element.get("center", {})
        lat = center.get("lat")
        lon = center.get("lon")
    else:
        return None  # relations not handled yet

    if lat is None or lon is None:
        return None

    return {
        "osm_id": element.get("id"),
        "osm_type": osm_type,
        "amenity_type": amenity_type,
        "name": tags.get("name"),
        "latitude": float(lat),
        "longitude": float(lon),
        "opening_hours": tags.get("opening_hours"),
        "wheelchair": tags.get("wheelchair"),
        "tags": json.dumps(tags, ensure_ascii=False),
        "ingested_at": ingested_at,
    }


def ingest(
    amenity_types: list[str] | None = None,
) -> pd.DataFrame:
    """
    Ingest OSM amenities for Paris into the Bronze layer.

    Parameters
    ----------
    amenity_types : list[str], optional
        Subset of ["bar", "nightclub", "park"]. Defaults to all three.

    Returns
    -------
    pd.DataFrame
        Combined DataFrame (also written to Parquet, one file per amenity_type).
    """
    logger = get_logger("osm", LOG_DIR)
    types_to_fetch = amenity_types or list(AMENITY_FILTERS.keys())
    ingested_at = datetime.now(timezone.utc)

    unknown = set(types_to_fetch) - set(AMENITY_FILTERS)
    if unknown:
        raise ValueError(f"Unknown amenity types: {unknown}. Valid: {set(AMENITY_FILTERS)}")

    session = build_session(retries=3, backoff_factor=2.0, timeout=OSM_HTTP_TIMEOUT)
    all_frames: list[pd.DataFrame] = []

    logger.info("OSM ingestion started — amenity_types=%s", types_to_fetch)

    for amenity_type in types_to_fetch:
        tag_filter = AMENITY_FILTERS[amenity_type]
        logger.info("  Fetching '%s' (filter: %s)", amenity_type, tag_filter)

        elements = _fetch_amenity(session, logger, amenity_type, tag_filter)
        logger.info("  Overpass returned %d elements for '%s'", len(elements), amenity_type)

        rows = [
            row
            for el in elements
            if (row := _element_to_row(el, amenity_type, ingested_at)) is not None
        ]

        if not rows:
            logger.warning("  No valid rows for amenity_type='%s'", amenity_type)
            continue

        df = pd.DataFrame(rows)[BRONZE_COLUMNS]

        # Deduplicate by osm_id (Overpass may return duplicates across node/way)
        before = len(df)
        df = df.drop_duplicates(subset=["osm_id", "osm_type"])
        if len(df) < before:
            logger.debug("  Dropped %d duplicate elements", before - len(df))

        path = save_parquet(
            df,
            source="osm",
            partition_col="amenity_type",
            partition_value=amenity_type,
            filename="part-0.parquet",
        )
        logger.info("  Saved %d rows → %s", len(df), path)
        all_frames.append(df)

    if not all_frames:
        logger.warning("OSM ingestion produced no rows.")
        return pd.DataFrame(columns=BRONZE_COLUMNS)

    df_all = pd.concat(all_frames, ignore_index=True)
    logger.info("OSM ingestion complete — %d total rows", len(df_all))
    return df_all
