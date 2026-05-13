"""
Gold Layer Builder
==================
Builds final, API-ready tables from Silver layer.
Includes denormalization for fast queries, pre-computed aggregations, and cache-friendly formats.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from src.ingestion.base import get_logger, read_parquet, save_parquet

LOG_DIR = Path(__file__).parents[2] / "logs"


def build_arrondissement_summary() -> pd.DataFrame:
    """
    Build a denormalized summary table for each arrondissement.
    Combines scores, amenities, and latest price data.
    """
    logger = get_logger("gold_builder", LOG_DIR)

    # Load Silver tables
    scores = read_parquet("silver")  # Will read all Silver tables
    # For now, we load specific files
    try:
        scores_df = pd.read_parquet(
            Path(__file__).parents[2] / "data" / "silver" / "scores_by_arrondissement.parquet"
        )
    except FileNotFoundError:
        logger.warning("scores_by_arrondissement.parquet not found")
        scores_df = pd.DataFrame()

    try:
        prices_df = pd.read_parquet(
            Path(__file__).parents[2] / "data" / "silver" / "prices_by_arrondissement_year.parquet"
        )
        # Get latest year only
        if not prices_df.empty:
            latest_year = prices_df["year"].max()
            prices_df = prices_df[prices_df["year"] == latest_year].drop(columns=["year"])
    except FileNotFoundError:
        logger.warning("prices_by_arrondissement_year.parquet not found")
        prices_df = pd.DataFrame()

    try:
        amenities_df = pd.read_parquet(
            Path(__file__).parents[2] / "data" / "silver" / "amenities_by_arrondissement.parquet"
        )
    except FileNotFoundError:
        logger.warning("amenities_by_arrondissement.parquet not found")
        amenities_df = pd.DataFrame()

    # Merge on arrondissement
    result = pd.DataFrame({"arrondissement": range(1, 21)})  # 1-20
    if not scores_df.empty:
        result = result.merge(scores_df, on="arrondissement", how="left")
    if not prices_df.empty:
        result = result.merge(prices_df, on="arrondissement", how="left")
    if not amenities_df.empty:
        result = result.merge(amenities_df, on="arrondissement", how="left")

    result["updated_at"] = datetime.now(timezone.utc)
    return result


def build_poi_catalog() -> pd.DataFrame:
    """
    Build a catalog of all Points of Interest (POI) with normalized fields for frontend.
    """
    osm_df = read_parquet("osm")
    if osm_df.empty:
        return pd.DataFrame()

    # Rename and select key columns
    poi_df = osm_df[[
        "osm_id",
        "osm_type",
        "amenity_type",
        "name",
        "latitude",
        "longitude",
        "opening_hours",
        "wheelchair",
    ]].copy()

    poi_df.columns = [
        "id",
        "type",
        "category",
        "name",
        "lat",
        "lon",
        "hours",
        "wheelchair_accessible",
    ]

    return poi_df


def build_price_timeline() -> pd.DataFrame:
    """
    Build a time-series table of median prices by arrondissement and year.
    Used for the timeline slider in the frontend.
    """
    prices_df = pd.read_parquet(
        Path(__file__).parents[2] / "data" / "silver" / "prices_by_arrondissement_year.parquet"
    )

    # Select year and median_price for the timeline
    timeline = prices_df[[
        "arrondissement",
        "year",
        "median_price",
        "transaction_count",
    ]].copy()

    return timeline


def build_gold_layer() -> None:
    """Orchestrate Gold layer build: create all final tables."""
    logger = get_logger("gold_builder", LOG_DIR)
    logger.info("Building Gold layer")

    # Arrondissement summary (main dashboard table)
    summary = build_arrondissement_summary()
    save_parquet(summary, source="gold", filename="arrondissement_summary.parquet")
    logger.info("Saved arrondissement summary for %d arrondissements", len(summary))

    # POI catalog (for map layers)
    poi = build_poi_catalog()
    save_parquet(poi, source="gold", filename="poi_catalog.parquet")
    logger.info("Saved POI catalog with %d points", len(poi))

    # Price timeline (for time slider)
    timeline = build_price_timeline()
    save_parquet(timeline, source="gold", filename="price_timeline.parquet")
    logger.info("Saved price timeline with %d year-arrond combinations", len(timeline))

    logger.info("Gold layer build complete")
