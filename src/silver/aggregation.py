"""
Silver Layer — Orchestration complète
======================================
Orchestre dans l'ordre :
  1. Agrégations indicateurs stratégiques (indicators.py)
  2. Scores normalisés 0-100 (scoring.py)
  3. Agrégations historiques DVF + OSM amenities

Toutes les sorties sont persistées dans data/silver/.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

import pandas as pd

from src.ingestion.base import get_logger, read_parquet, save_parquet
from .indicators import build_all_indicator_silvers
from .scoring import ArrondissementScorer

LOG_DIR = Path(__file__).parents[2] / "logs"
SILVER_ROOT = Path(__file__).parents[2] / "data" / "silver"


# ---------------------------------------------------------------------------
# Agrégations historiques (DVF, OSM amenities) — inchangées
# ---------------------------------------------------------------------------

def aggregate_prices_by_arrondissement() -> pd.DataFrame:
    """Agrège les prix DVF : moyenne, médiane, comptage par arrondissement × année."""
    dvf_df = read_parquet("dvf")
    if dvf_df.empty:
        return pd.DataFrame()

    dvf_df["arrondissement"] = dvf_df["code_postal"].astype(str).str[-2:].astype(int)
    dvf_df["year"] = pd.to_datetime(dvf_df["date_mutation"], errors="coerce").dt.year

    grouped = (
        dvf_df.groupby(["arrondissement", "year"])
        .agg({
            "valeur_fonciere":   ["count", "mean", "median", "min", "max"],
            "surface_reelle_bati": "mean",
        })
        .reset_index()
    )
    grouped.columns = [
        "arrondissement", "year",
        "transaction_count", "mean_price", "median_price",
        "min_price", "max_price", "mean_area",
    ]
    return grouped


def aggregate_amenities_by_arrondissement() -> pd.DataFrame:
    """Compte les POI OSM par arrondissement (sjoin spatial existant)."""
    import geopandas as gpd

    osm_df = read_parquet("osm")
    if osm_df.empty:
        return pd.DataFrame()

    boundaries_df = read_parquet("boundaries")
    if boundaries_df.empty:
        return pd.DataFrame()

    osm_gdf = gpd.GeoDataFrame(
        osm_df,
        geometry=gpd.points_from_xy(osm_df["longitude"], osm_df["latitude"]),
        crs="EPSG:4326",
    )
    geom_col = "geometry_wkt" if "geometry_wkt" in boundaries_df.columns else "geometry"
    boundaries_gdf = gpd.GeoDataFrame(
        boundaries_df,
        geometry=gpd.GeoSeries.from_wkt(boundaries_df[geom_col]),
        crs="EPSG:4326",
    )

    joined = gpd.sjoin(
        osm_gdf, boundaries_gdf[["arrondissement", "geometry"]],
        how="inner", predicate="within",
    )
    counts = (
        joined.groupby(["arrondissement", "amenity_type"])
        .size()
        .unstack(fill_value=0)
        .reset_index()
    )
    for col in ["bar", "nightclub", "park"]:
        if col not in counts.columns:
            counts[col] = 0

    return counts.rename(columns={
        "bar": "bar_count",
        "nightclub": "nightclub_count",
        "park": "park_count",
    })[["arrondissement", "bar_count", "nightclub_count", "park_count"]]


# ---------------------------------------------------------------------------
# Orchestrateur principal
# ---------------------------------------------------------------------------

def build_silver_layer() -> None:
    """
    Construit l'intégralité de la couche Silver dans l'ordre :
      1. Tables Silver indicateurs (connectivity, mobility, health_env, tranquility)
      2. Scores normalisés (anime, calme, accessibilité + 4 nouveaux)
      3. Agrégations historiques (prix DVF, comptages OSM)
    """
    logger = get_logger("silver_aggregation", LOG_DIR)
    started = time.perf_counter()
    logger.info("=" * 60)
    logger.info("Silver layer — démarrage")
    logger.info("=" * 60)

    SILVER_ROOT.mkdir(parents=True, exist_ok=True)

    # --- Étape 1 : Tables indicateurs (spatial joins) ---
    logger.info(">>> Étape 1/3 : Agrégations spatiales indicateurs")
    try:
        indicator_frames = build_all_indicator_silvers(logger)
        logger.info(
            "Indicateurs Silver : %d tables produites (%s)",
            len(indicator_frames),
            ", ".join(f"{k}={len(v)}" for k, v in indicator_frames.items()),
        )
    except Exception as exc:
        logger.error("Erreur indicateurs Silver : %s", exc, exc_info=True)

    # --- Étape 2 : Scores normalisés ---
    logger.info(">>> Étape 2/3 : Calcul des scores 0-100")
    try:
        scorer = ArrondissementScorer(logger)
        scores = scorer.compute_all_scores()
        _save(scores, "scores_by_arrondissement.parquet", logger)
    except Exception as exc:
        logger.error("Erreur scoring Silver : %s", exc, exc_info=True)
        scores = pd.DataFrame()

    # --- Étape 3 : Agrégations historiques ---
    logger.info(">>> Étape 3/3 : Agrégations historiques (DVF + OSM)")
    try:
        prices = aggregate_prices_by_arrondissement()
        if not prices.empty:
            _save(prices, "prices_by_arrondissement_year.parquet", logger)
    except Exception as exc:
        logger.error("Erreur agrégation prix : %s", exc, exc_info=True)

    try:
        amenities = aggregate_amenities_by_arrondissement()
        if not amenities.empty:
            _save(amenities, "amenities_by_arrondissement.parquet", logger)
    except Exception as exc:
        logger.error("Erreur agrégation amenities : %s", exc, exc_info=True)

    elapsed = time.perf_counter() - started
    logger.info("=" * 60)
    logger.info("Silver layer complet (%.1fs)", elapsed)
    logger.info("=" * 60)


def _save(df: pd.DataFrame, filename: str, logger: logging.Logger) -> None:
    path = SILVER_ROOT / filename
    df.to_parquet(path, index=False, engine="pyarrow", compression="snappy")
    logger.info("  Sauvegardé : %s (%d lignes)", path.name, len(df))
