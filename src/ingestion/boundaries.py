"""
Paris Arrondissement Boundaries Bronze Ingestion
=================================================
Source  : Paris Open Data – Explore v2.1 API
Endpoint: https://opendata.paris.fr/api/explore/v2.1/catalog/datasets/
          arrondissements/exports/geojson

Downloads the GeoJSON boundary file for the 20 Paris arrondissements and stores
it both as Parquet (attribute table) and as a raw GeoJSON file for use in
downstream spatial joins.

Bronze schema
-------------
arrondissement      int      1–20
c_ar                str      INSEE arrondissement code (e.g. "1")
c_arinsee           str      Full INSEE code (e.g. "75101")
l_ar                str      Label (e.g. "1er arrondissement")
surface_ha          float    area in hectares
centroid_lat        float    polygon centroid latitude
centroid_lon        float    polygon centroid longitude
geometry_wkt        str      WKT polygon (for Parquet consumers without GeoParquet)
ingested_at         datetime
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .base import BRONZE_ROOT, build_session, get_logger, save_parquet

BOUNDARIES_URL = (
    "https://opendata.paris.fr/api/explore/v2.1/catalog/datasets/"
    "arrondissements/exports/geojson?lang=fr"
)
LOG_DIR = Path(__file__).parents[2] / "logs"

BRONZE_COLUMNS = [
    "arrondissement",
    "c_ar",
    "c_arinsee",
    "l_ar",
    "surface_ha",
    "centroid_lat",
    "centroid_lon",
    "geometry_wkt",
    "ingested_at",
]


def _polygon_centroid(geometry: dict) -> tuple[float | None, float | None]:
    """Naïve centroid: mean of all exterior ring coordinates."""
    try:
        gtype = geometry.get("type")
        if gtype == "Polygon":
            ring = geometry["coordinates"][0]
        elif gtype == "MultiPolygon":
            ring = geometry["coordinates"][0][0]
        else:
            return None, None
        lons = [c[0] for c in ring]
        lats = [c[1] for c in ring]
        return sum(lats) / len(lats), sum(lons) / len(lons)
    except (KeyError, IndexError, ZeroDivisionError):
        return None, None


def _geometry_to_wkt(geometry: dict) -> str | None:
    """Minimal GeoJSON→WKT conversion (Polygon only, for Parquet compatibility)."""
    try:
        gtype = geometry.get("type")
        if gtype == "Polygon":
            rings = geometry["coordinates"]
            parts = ", ".join(
                "(" + ", ".join(f"{c[0]} {c[1]}" for c in ring) + ")"
                for ring in rings
            )
            return f"POLYGON ({parts})"
        elif gtype == "MultiPolygon":
            polys = []
            for poly in geometry["coordinates"]:
                rings_str = ", ".join(
                    "(" + ", ".join(f"{c[0]} {c[1]}" for c in ring) + ")"
                    for ring in poly
                )
                polys.append(f"({rings_str})")
            return f"MULTIPOLYGON ({', '.join(polys)})"
        return None
    except (KeyError, IndexError):
        return None


def ingest() -> pd.DataFrame:
    """
    Ingest Paris arrondissement boundaries.

    Saves:
      - data/bronze/boundaries/part-0.parquet  (attribute table + WKT)
      - data/bronze/boundaries/arrondissements.geojson  (raw GeoJSON)
    """
    logger = get_logger("boundaries", LOG_DIR)
    ingested_at = datetime.now(timezone.utc)
    session = build_session()

    logger.info("Boundaries ingestion started")
    resp = session.get(BOUNDARIES_URL)

    if resp.status_code != 200:
        logger.error("Boundaries API → HTTP %d: %s", resp.status_code, resp.text[:300])
        return pd.DataFrame(columns=BRONZE_COLUMNS)

    geojson = resp.json()

    # Save raw GeoJSON for spatial consumers
    raw_path = BRONZE_ROOT / "boundaries" / "arrondissements.geojson"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(json.dumps(geojson, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Raw GeoJSON saved → %s", raw_path)

    rows = []
    for feature in geojson.get("features", []):
        props = feature.get("properties", {})
        geom = feature.get("geometry", {})
        clat, clon = _polygon_centroid(geom)
        rows.append({
            "arrondissement": int(props.get("c_ar", 0)),
            "c_ar": str(props.get("c_ar", "")),
            "c_arinsee": str(props.get("c_arinsee", "")),
            "l_ar": props.get("l_ar"),
            "surface_ha": float(props["surface"]) if props.get("surface") else None,
            "centroid_lat": clat,
            "centroid_lon": clon,
            "geometry_wkt": _geometry_to_wkt(geom),
            "ingested_at": ingested_at,
        })

    if not rows:
        logger.warning("Boundaries ingestion returned no features.")
        return pd.DataFrame(columns=BRONZE_COLUMNS)

    df = pd.DataFrame(rows)[BRONZE_COLUMNS].sort_values("arrondissement").reset_index(drop=True)
    path = save_parquet(df, source="boundaries", filename="part-0.parquet")
    logger.info("Saved %d arrondissements → %s", len(df), path)
    return df
