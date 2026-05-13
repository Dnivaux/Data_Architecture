"""
/api/poi/* routes – Points of Interest (bars, parks, etc.).
"""
from fastapi import APIRouter, HTTPException, Query

from api.dependencies import load_gold_table
from api.schemas import POI

router = APIRouter(prefix="/poi", tags=["poi"])


@router.get(
    "/",
    response_model=list[POI],
    summary="All POI",
    description="Returns all Points of Interest. Optionally filter by category.",
)
def get_poi(category: str | None = Query(None, description="Filter by: bar, nightclub, or park")):
    """Fetch all POI, optionally filtered by category."""
    df = load_gold_table("poi_catalog.parquet")
    if df.empty:
        raise HTTPException(status_code=503, detail="POI data not available")

    if category:
        df = df[df["category"] == category.lower()]

    results = []
    for _, row in df.iterrows():
        results.append(POI(
            id=int(row["id"]),
            type=str(row.get("type", "unknown")),
            category=str(row.get("category", "unknown")),
            name=row.get("name"),
            lat=float(row["lat"]),
            lon=float(row["lon"]),
            hours=row.get("hours"),
            wheelchair_accessible=row.get("wheelchair_accessible"),
        ))
    return results


@router.get(
    "/by-category/{category}",
    response_model=list[POI],
    summary="POI by category",
    description="Get all bars, nightclubs, or parks.",
)
def get_poi_by_category(category: str = Query(..., regex="^(bar|nightclub|park)$")):
    """Fetch POI filtered by category."""
    df = load_gold_table("poi_catalog.parquet")
    if df.empty:
        raise HTTPException(status_code=503, detail="POI data not available")

    df = df[df["category"] == category.lower()]

    results = []
    for _, row in df.iterrows():
        results.append(POI(
            id=int(row["id"]),
            type=str(row.get("type", "unknown")),
            category=str(row.get("category", "unknown")),
            name=row.get("name"),
            lat=float(row["lat"]),
            lon=float(row["lon"]),
            hours=row.get("hours"),
            wheelchair_accessible=row.get("wheelchair_accessible"),
        ))
    return results
