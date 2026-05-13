"""
Silver Layer Scoring
====================
Spatial processing & scoring with GeoPandas to compute:
  - "Animé" (Lively): density of bars, nightclubs, cultural POI
  - "Calme" (Calm): inverse of crime + good air quality
  - "Accessibilité financière" (Financial accessibility): inverse of median price + social housing %

Outputs GeoDataFrames aggregated by arrondissement (or IRIS).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

from src.ingestion.base import BRONZE_ROOT, get_logger, read_parquet

LOG_DIR = Path(__file__).parents[2] / "logs"
SILVER_ROOT = Path(__file__).parents[2] / "data" / "silver"


def _normalize_score(series: pd.Series, min_val: float = 0.0, max_val: float = 100.0) -> pd.Series:
    """Min-max normalize a series to [min_val, max_val]. Handles NaN gracefully."""
    s = series.fillna(0)
    if s.max() == s.min():
        return pd.Series([50.0] * len(s), index=s.index)
    normalized = (s - s.min()) / (s.max() - s.min())
    return normalized * (max_val - min_val) + min_val


class ArrondissementScorer:
    """Compute livability scores by arrondissement."""

    def __init__(self, logger: logging.Logger | None = None):
        self.logger = logger or get_logger("scoring", LOG_DIR)
        self.boundaries_gdf = self._load_boundaries()

    def _load_boundaries(self) -> gpd.GeoDataFrame:
        """Load arrondissement boundaries as GeoDataFrame."""
        df = read_parquet("boundaries")
        if df.empty:
            self.logger.error("No boundaries data found. Run ingestion first.")
            return gpd.GeoDataFrame()

        # Convert WKT to geometry
        if "geometry_wkt" in df.columns:
            df = df.copy()
            df["geometry"] = gpd.GeoSeries.from_wkt(df["geometry_wkt"])

        gdf = gpd.GeoDataFrame(df, geometry="geometry", crs="EPSG:4326")
        self.logger.info("Loaded %d arrondissements", len(gdf))
        return gdf

    def score_anime(self) -> pd.DataFrame:
        """
        "Animé" (Lively) score: density of bars, nightclubs, cultural activities.
        Based on OSM POI counts.
        """
        osm_df = read_parquet("osm")
        if osm_df.empty:
            self.logger.warning("No OSM data for 'Animé' score")
            return pd.DataFrame()

        osm_gdf = gpd.GeoDataFrame(
            osm_df,
            geometry=gpd.points_from_xy(osm_df["longitude"], osm_df["latitude"]),
            crs="EPSG:4326",
        )

        # Count POI by type
        counts = {}
        for arrond in self.boundaries_gdf["arrondissement"].values:
            bounds = self.boundaries_gdf[self.boundaries_gdf["arrondissement"] == arrond]
            if bounds.empty:
                continue

            # Points-in-polygon: which OSM features fall in this arrondissement?
            poi_in_arrond = gpd.sjoin(osm_gdf, bounds, how="inner", predicate="within")
            counts[arrond] = {
                "bar_count": len(poi_in_arrond[poi_in_arrond["amenity_type"] == "bar"]),
                "nightclub_count": len(poi_in_arrond[poi_in_arrond["amenity_type"] == "nightclub"]),
                "park_count": len(poi_in_arrond[poi_in_arrond["amenity_type"] == "park"]),
            }

        df = pd.DataFrame(counts).T.fillna(0)
        df["total_poi"] = df["bar_count"] + df["nightclub_count"] + df["park_count"]
        df["anime_score"] = _normalize_score(df["total_poi"], 0, 100)
        return df[["bar_count", "nightclub_count", "park_count", "total_poi", "anime_score"]].reset_index(names=["arrondissement"])

    def score_calme(self) -> pd.DataFrame:
        """
        "Calme" (Calm) score: inverse of crime + good air quality.
        Crime: fewer incidents is better. Air quality: lower pollutant values is better.
        """
        # For now, we'll compute a placeholder since crime & air_quality data stubs exist
        # Once they're implemented, real aggregation will happen
        calme_scores = []
        for arrond in self.boundaries_gdf["arrondissement"].values:
            # TODO: read crime data and aggregate by arrondissement
            # TODO: read air_quality data and compute mean pollutant levels
            # For now, placeholder score
            calme_scores.append({
                "arrondissement": arrond,
                "crime_incidents": 0,  # placeholder
                "air_quality_index": 50,  # placeholder (0-100, lower=better air)
                "calme_score": 75,  # placeholder
            })
        return pd.DataFrame(calme_scores)

    def score_accessibilite(self) -> pd.DataFrame:
        """
        "Accessibilité financière" (Financial accessibility): inverse of median price + social housing %.
        Higher score = more affordable.
        """
        dvf_df = read_parquet("dvf")
        if dvf_df.empty:
            self.logger.warning("No DVF data for 'Accessibilité' score")
            return pd.DataFrame()

        dvf_gdf = gpd.GeoDataFrame(
            dvf_df,
            geometry=gpd.points_from_xy(dvf_df["longitude"], dvf_df["latitude"]),
            crs="EPSG:4326",
        )

        accessibilite_scores = []
        for arrond in self.boundaries_gdf["arrondissement"].values:
            bounds = self.boundaries_gdf[self.boundaries_gdf["arrondissement"] == arrond]
            if bounds.empty:
                continue

            dvf_in_arrond = gpd.sjoin(dvf_gdf, bounds, how="inner", predicate="within")
            if dvf_in_arrond.empty:
                median_price = None
            else:
                median_price = dvf_in_arrond["valeur_fonciere"].median()

            # TODO: Once revenus is implemented, get social_housing_percent
            social_housing_pct = 0  # placeholder

            # Inverse of price: lower price → higher accessibility
            price_score = 100 - (_normalize_score(pd.Series([median_price or 0]))[0] if median_price else 50)
            housing_score = 50 + (social_housing_pct * 0.5)  # Higher social housing = better accessibility
            accessibilite_scores.append({
                "arrondissement": arrond,
                "median_price": median_price,
                "social_housing_pct": social_housing_pct,
                "accessibilite_score": (price_score + housing_score) / 2,
            })

        return pd.DataFrame(accessibilite_scores)

    def compute_all_scores(self) -> pd.DataFrame:
        """Compute all three livability scores and combine into one DataFrame."""
        self.logger.info("Computing all livability scores")

        anime = self.score_anime()
        calme = self.score_calme()
        accessibilite = self.score_accessibilite()

        # Merge on arrondissement
        result = (
            self.boundaries_gdf[["arrondissement"]].copy()
            .merge(anime, on="arrondissement", how="left")
            .merge(calme, on="arrondissement", how="left")
            .merge(accessibilite, on="arrondissement", how="left")
        )

        result["ingested_at"] = datetime.now(timezone.utc)
        return result
