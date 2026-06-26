"""
Paris IRIS Boundaries Bronze Ingestion
======================================
Source  : OpenDataSoft Data Hub — dataset « Contours…Iris® » (Île-de-France)
Endpoint: https://data.opendatasoft.com/api/explore/v2.1/catalog/datasets/
          iris@datailedefrance/exports/geojson?where=dep%3D%2275%22

Télécharge le GeoJSON des ~992 IRIS parisiens (mailles infra-communales INSEE)
et le persiste en Parquet (table attributaire + WKT) ainsi qu'en GeoJSON brut
pour les jointures spatiales en aval.

Pourquoi l'IRIS ?
-----------------
L'arrondissement (20 zones) masque toute hétérogénéité interne. L'IRIS est le
plus petit maillage statistique de l'INSEE (~2000 habitants) : il rend visible
le détail *à l'intérieur* d'un arrondissement. L'arrondissement est conservé
comme dimension parente (`arrondissement = int(insee_com[-2:])`).

Bronze schema
-------------
code_iris       str      Code IRIS INSEE 9 chiffres (ex. "751010402")
nom_iris        str      Libellé IRIS (ex. "Place Vendôme 2")
arrondissement  int      1–20 (déduit de insee_com)
insee_com       str      Code commune INSEE (ex. "75101")
typ_iris        str      Type IRIS INSEE (H/A/D/Z)
surface_ha      float    aire en hectares (si fournie)
centroid_lat    float    latitude du centroïde (naïf)
centroid_lon    float    longitude du centroïde (naïf)
geometry_wkt    str      Polygone/MultiPolygone WKT EPSG:4326
ingested_at     datetime
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .base import BRONZE_ROOT, build_session, get_logger, save_parquet
from .boundaries import _geometry_to_wkt, _polygon_centroid

IRIS_URL = (
    "https://data.opendatasoft.com/api/explore/v2.1/catalog/datasets/"
    "iris@datailedefrance/exports/geojson?where=dep%3D%2275%22&lang=fr"
)
LOG_DIR = Path(__file__).parents[2] / "logs"

BRONZE_COLUMNS = [
    "code_iris",
    "nom_iris",
    "arrondissement",
    "insee_com",
    "typ_iris",
    "surface_ha",
    "centroid_lat",
    "centroid_lon",
    "geometry_wkt",
    "ingested_at",
]


def _arrondissement_from_insee(insee_com: str | int | None) -> int | None:
    """Déduit l'arrondissement (1-20) du code commune INSEE (ex. 75101 → 1)."""
    if insee_com is None:
        return None
    code = str(insee_com).strip()
    if len(code) < 2 or not code[-2:].isdigit():
        return None
    arr = int(code[-2:])
    return arr if 1 <= arr <= 20 else None


def ingest() -> pd.DataFrame:
    """
    Ingest des contours IRIS parisiens.

    Saves:
      - data/bronze/iris_boundaries/part-0.parquet  (table attributaire + WKT)
      - data/bronze/iris_boundaries/iris.geojson    (GeoJSON brut)
    """
    logger = get_logger("iris_boundaries", LOG_DIR)
    ingested_at = datetime.now(timezone.utc)
    session = build_session()

    logger.info("IRIS boundaries ingestion started")
    resp = session.get(IRIS_URL)

    if resp.status_code != 200:
        logger.error("IRIS API → HTTP %d: %s", resp.status_code, resp.text[:300])
        return pd.DataFrame(columns=BRONZE_COLUMNS)

    geojson = resp.json()

    # Sauvegarde du GeoJSON brut pour les consommateurs spatiaux / le front
    raw_path = BRONZE_ROOT / "iris_boundaries" / "iris.geojson"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(json.dumps(geojson, ensure_ascii=False), encoding="utf-8")
    logger.info("Raw GeoJSON saved → %s", raw_path)

    rows = []
    for feature in geojson.get("features", []):
        props = feature.get("properties", {})
        geom = feature.get("geometry", {})
        clat, clon = _polygon_centroid(geom)
        insee_com = props.get("insee_com")
        rows.append({
            "code_iris": str(props.get("code_iris", "")),
            "nom_iris": props.get("nom_iris"),
            "arrondissement": _arrondissement_from_insee(insee_com),
            "insee_com": str(insee_com) if insee_com is not None else None,
            "typ_iris": props.get("typ_iris"),
            "surface_ha": (
                float(props["surf_iris"]) if props.get("surf_iris")
                else (float(props["surface"]) / 10_000 if props.get("surface") else None)
            ),
            "centroid_lat": clat,
            "centroid_lon": clon,
            "geometry_wkt": _geometry_to_wkt(geom),
            "ingested_at": ingested_at,
        })

    if not rows:
        logger.warning("IRIS ingestion returned no features.")
        return pd.DataFrame(columns=BRONZE_COLUMNS)

    df = (
        pd.DataFrame(rows)[BRONZE_COLUMNS]
        .dropna(subset=["code_iris"])
        .sort_values(["arrondissement", "code_iris"])
        .reset_index(drop=True)
    )
    path = save_parquet(df, source="iris_boundaries", filename="part-0.parquet")
    logger.info(
        "Saved %d IRIS (%d arrondissements) → %s",
        len(df), df["arrondissement"].nunique(), path,
    )
    return df


if __name__ == "__main__":
    out = ingest()
    if not out.empty:
        print(out.head())
        print(f"\n{len(out)} IRIS, {out['arrondissement'].nunique()} arrondissements")
