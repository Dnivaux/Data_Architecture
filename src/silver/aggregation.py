"""
Silver Layer Aggregation
========================
Aggregate Bronze data by arrondissement and produce dimensional tables for Gold layer.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from src.ingestion.base import get_logger, read_parquet, save_parquet
from .scoring import ArrondissementScorer

LOG_DIR = Path(__file__).parents[2] / "logs"


def aggregate_prices_by_arrondissement() -> pd.DataFrame:
    """Aggregate DVF prices: mean, median, count by arrondissement."""
    dvf_df = read_parquet("dvf")
    if dvf_df.empty:
        return pd.DataFrame()

    # Map code_postal to arrondissement (750XX -> XX)
    dvf_df["arrondissement"] = dvf_df["code_postal"].str[-2:].astype(int)
    dvf_df["year"] = pd.to_datetime(dvf_df["date_mutation"]).dt.year

    grouped = (
        dvf_df.groupby(["arrondissement", "year"])
        .agg({
            "valeur_fonciere": ["count", "mean", "median", "min", "max"],
            "surface_reelle_bati": "mean",
        })
        .reset_index()
    )
    grouped.columns = [
        "arrondissement",
        "year",
        "transaction_count",
        "mean_price",
        "median_price",
        "min_price",
        "max_price",
        "mean_area",
    ]
    return grouped


def aggregate_amenities_by_arrondissement() -> pd.DataFrame:
    """Count OSM amenities by arrondissement and type."""
    import geopandas as gpd

    osm_df = read_parquet("osm")
    if osm_df.empty:
        return pd.DataFrame()

    boundaries_df = read_parquet("boundaries")
    if boundaries_df.empty:
        return pd.DataFrame()

    # Convert to GeoDataFrame
    osm_gdf = gpd.GeoDataFrame(
        osm_df,
        geometry=gpd.points_from_xy(osm_df["longitude"], osm_df["latitude"]),
        crs="EPSG:4326",
    )
    boundaries_gdf = gpd.GeoDataFrame(
        boundaries_df,
        geometry=gpd.GeoSeries.from_wkt(boundaries_df["geometry_wkt"]),
        crs="EPSG:4326",
    )

    result = []
    for _, boundary in boundaries_gdf.iterrows():
        arrond = boundary["arrondissement"]
        poi_in_arrond = osm_gdf[osm_gdf.geometry.within(boundary["geometry"])]
        counts = poi_in_arrond.groupby("amenity_type").size().to_dict()
        result.append({
            "arrondissement": arrond,
            "bar_count": counts.get("bar", 0),
            "nightclub_count": counts.get("nightclub", 0),
            "park_count": counts.get("park", 0),
        })

    return pd.DataFrame(result)


def build_silver_layer() -> None:
    """Orchestrate Silver layer: compute scores + aggregate tables."""
    logger = get_logger("silver_aggregation", LOG_DIR)
    logger.info("Building Silver layer")

    # Compute livability scores
    scorer = ArrondissementScorer(logger)
    scores = scorer.compute_all_scores()
    save_parquet(scores, source="silver", filename="scores_by_arrondissement.parquet")
    logger.info("Saved livability scores for %d arrondissements", len(scores))

    # Aggregate prices
    prices = aggregate_prices_by_arrondissement()
    if not prices.empty:
        save_parquet(prices, source="silver", filename="prices_by_arrondissement_year.parquet")
        logger.info("Saved price aggregations for %d arrond-year combinations", len(prices))

    # Aggregate amenities
    amenities = aggregate_amenities_by_arrondissement()
    if not amenities.empty:
        save_parquet(amenities, source="silver", filename="amenities_by_arrondissement.parquet")
        logger.info("Saved amenity counts for %d arrondissements", len(amenities))

    logger.info("Silver layer build complete")
