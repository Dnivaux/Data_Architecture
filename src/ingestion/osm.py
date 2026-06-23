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
    "https://overpass.openstreetmap.fr/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass-api.de/api/interpreter",
]
OVERPASS_URLS = [url for url in OVERPASS_URLS if url]
LOG_DIR = Path(__file__).parents[2] / "logs"
OSM_HTTP_TIMEOUT = int(os.environ.get("OSM_HTTP_TIMEOUT", "120"))
OSM_QUERY_TIMEOUT = int(os.environ.get("OSM_QUERY_TIMEOUT", "120"))
OSM_MAX_WORKERS = int(os.environ.get("OSM_MAX_WORKERS", "4"))
OSM_STRATEGY = os.environ.get("OSM_STRATEGY", "combined")  # "combined" or "threaded"
OSM_USE_BBOX = os.environ.get("OSM_USE_BBOX", "true").lower() in ("true", "1", "yes")
OSM_BBOX = os.environ.get("OSM_BBOX", "48.812,2.224,48.903,2.470")

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
    if OSM_USE_BBOX:
        return f"""[out:json][timeout:{OSM_QUERY_TIMEOUT}];
(
  node[{tag_filter}]({OSM_BBOX});
  way[{tag_filter}]({OSM_BBOX});
);
out center tags;""".strip()
    else:
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


def _fetch_combined_amenities(
    session: Any,
    logger: logging.Logger,
    types_to_fetch: list[str],
) -> list[dict]:
    """Fetch multiple amenities in a single combined Overpass query."""
    query_parts = []
    for amenity_type in types_to_fetch:
        tag_filter = AMENITY_FILTERS[amenity_type]
        if OSM_USE_BBOX:
            query_parts.append(f"  node[{tag_filter}]({OSM_BBOX});")
            query_parts.append(f"  way[{tag_filter}]({OSM_BBOX});")
        else:
            query_parts.append(f"  node[{tag_filter}](area.paris);")
            query_parts.append(f"  way[{tag_filter}](area.paris);")

    query_body = "\n".join(query_parts)
    
    if OSM_USE_BBOX:
        query = f"""[out:json][timeout:{OSM_QUERY_TIMEOUT}];
(
{query_body}
);
out center tags;""".strip()
    else:
        query = f"""[out:json][timeout:{OSM_QUERY_TIMEOUT}];
area["name"="Paris"]["admin_level"="8"]->.paris;
(
{query_body}
);
out center tags;""".strip()

    logger.debug("Combined Overpass query:\n%s", query)

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
                "Combined OSM query → HTTP %d (%s): %s",
                resp.status_code, url, resp.text[:300],
            )
            continue

        try:
            data = resp.json()
        except ValueError as exc:
            logger.warning("Combined OSM query JSON parse error (%s): %s", url, exc)
            continue

        return data.get("elements", [])

    return []


def _classify_element(element: dict, allowed_types: list[str]) -> str | None:
    """Classify an OSM element into one of the allowed amenity types based on its tags."""
    tags = element.get("tags", {})
    
    # 1. Check amenity tag
    amenity = tags.get("amenity")
    if amenity in allowed_types:
        if amenity in ("bar", "cinema", "college", "nightclub", "restaurant", "school", "university"):
            return amenity

    # 2. Check leisure tag
    leisure = tags.get("leisure")
    if leisure in allowed_types:
        if leisure in ("park", "stadium"):
            return leisure

    # 3. Check shop tag
    if "shop" in allowed_types:
        shop = tags.get("shop")
        if shop in ("supermarket", "hypermarket", "convenience", "grocery", "department_store", "mall"):
            return "shop"

    return None


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


def _fetch_and_process_single_type(
    session: Any,
    logger: logging.Logger,
    amenity_type: str,
    ingested_at: datetime,
) -> pd.DataFrame:
    """Helper to fetch and process a single amenity type (for threaded execution)."""
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
        return pd.DataFrame(columns=BRONZE_COLUMNS)

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
    return df


def ingest(
    amenity_types: list[str] | None = None,
) -> pd.DataFrame:
    """
    Ingest OSM amenities for Paris into the Bronze layer.

    Parameters
    ----------
    amenity_types : list[str], optional
        Subset of the keys in AMENITY_FILTERS. Defaults to all keys.

    Returns
    -------
    pd.DataFrame
        Combined DataFrame (also written to Parquet, partitioned by amenity_type).
    """
    import concurrent.futures

    logger = get_logger("osm", LOG_DIR)
    types_to_fetch = amenity_types or list(AMENITY_FILTERS.keys())
    ingested_at = datetime.now(timezone.utc)

    unknown = set(types_to_fetch) - set(AMENITY_FILTERS)
    if unknown:
        raise ValueError(f"Unknown amenity types: {unknown}. Valid: {set(AMENITY_FILTERS)}")

    session = build_session(retries=3, backoff_factor=2.0, timeout=OSM_HTTP_TIMEOUT)
    all_frames: list[pd.DataFrame] = []

    logger.info(
        "OSM ingestion started — amenity_types=%s, strategy=%s, use_bbox=%s",
        types_to_fetch, OSM_STRATEGY, OSM_USE_BBOX,
    )

    if OSM_STRATEGY == "combined":
        try:
            logger.info("Attempting combined query strategy...")
            elements = _fetch_combined_amenities(session, logger, types_to_fetch)
            logger.info("Combined Overpass query returned %d elements", len(elements))
            
            if elements:
                rows = []
                for el in elements:
                    amenity_type = _classify_element(el, types_to_fetch)
                    if amenity_type is not None:
                        row = _element_to_row(el, amenity_type, ingested_at)
                        if row is not None:
                            rows.append(row)
                
                if rows:
                    df_all = pd.DataFrame(rows)
                    for amenity_type in types_to_fetch:
                        df_type = df_all[df_all["amenity_type"] == amenity_type]
                        if df_type.empty:
                            logger.warning("  No valid rows for amenity_type='%s'", amenity_type)
                            continue
                        
                        df_type = df_type[BRONZE_COLUMNS].drop_duplicates(subset=["osm_id", "osm_type"])
                        path = save_parquet(
                            df_type,
                            source="osm",
                            partition_col="amenity_type",
                            partition_value=amenity_type,
                            filename="part-0.parquet",
                        )
                        logger.info("  Saved %d rows → %s", len(df_type), path)
                        all_frames.append(df_type)
                
                if all_frames:
                    df_res = pd.concat(all_frames, ignore_index=True)
                    logger.info("OSM ingestion complete (combined) — %d total rows", len(df_res))
                    return df_res
            
            logger.warning("Combined query strategy produced no rows. Falling back to threaded strategy...")
        except Exception as exc:
            logger.warning("Combined query strategy failed (%s). Falling back to threaded strategy...", exc)

    # Threaded Strategy / Fallback
    logger.info("Running OSM ingestion with threaded strategy (max_workers=%d)", OSM_MAX_WORKERS)
    with concurrent.futures.ThreadPoolExecutor(max_workers=OSM_MAX_WORKERS) as executor:
        futures = {
            executor.submit(_fetch_and_process_single_type, session, logger, amenity_type, ingested_at): amenity_type
            for amenity_type in types_to_fetch
        }
        for future in concurrent.futures.as_completed(futures):
            amenity_type = futures[future]
            try:
                df = future.result()
                if not df.empty:
                    all_frames.append(df)
            except Exception as exc:
                logger.error("Thread for '%s' failed with error: %s", amenity_type, exc)

    if not all_frames:
        logger.warning("OSM ingestion produced no rows.")
        return pd.DataFrame(columns=BRONZE_COLUMNS)

    df_all = pd.concat(all_frames, ignore_index=True)
    logger.info("OSM ingestion complete (threaded) — %d total rows", len(df_all))
    return df_all
