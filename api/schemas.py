"""
Pydantic schemas for API request/response validation.
"""
from typing import Optional

from pydantic import BaseModel, Field


class ArrondissementScore(BaseModel):
    """Livability scores for a single arrondissement."""

    arrondissement: int = Field(..., ge=1, le=20)
    anime_score: float = Field(..., ge=0, le=100, description="Lively/vibrant score")
    calme_score: float = Field(..., ge=0, le=100, description="Calm/peaceful score")
    accessibilite_score: float = Field(..., ge=0, le=100, description="Financial accessibility score")
    bar_count: int = Field(default=0)
    nightclub_count: int = Field(default=0)
    park_count: int = Field(default=0)
    median_price: Optional[float] = Field(None, description="Median property price (€)")
    social_housing_pct: Optional[float] = Field(None, description="Percentage of social housing")


class POI(BaseModel):
    """Point of Interest (bar, park, etc.)."""

    id: int = Field(..., description="OSM element id")
    type: str = Field(..., description="node or way")
    category: str = Field(..., description="bar, nightclub, or park")
    name: Optional[str] = None
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    hours: Optional[str] = Field(None, description="Opening hours tag")
    wheelchair_accessible: Optional[str] = Field(None)


class PriceTimeline(BaseModel):
    """Median price for an arrondissement in a given year."""

    arrondissement: int = Field(..., ge=1, le=20)
    year: int = Field(..., ge=2014, le=2025)
    median_price: Optional[float]
    transaction_count: int


class ArrondissementComparison(BaseModel):
    """Comparison of two arrondissements."""

    arrond_a: int = Field(..., ge=1, le=20)
    arrond_b: int = Field(..., ge=1, le=20)
    scores_a: ArrondissementScore
    scores_b: ArrondissementScore
    price_diff: Optional[float] = Field(None, description="Price difference (B - A)")
    livability_diff: Optional[float] = Field(None, description="Average livability difference")


class HealthCheck(BaseModel):
    """Health check response."""

    status: str
    message: str
