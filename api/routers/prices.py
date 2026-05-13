"""
/api/prices/* routes – Property price timeline and historical data.
"""
from fastapi import APIRouter, HTTPException, Query

from api.dependencies import load_gold_table
from api.schemas import PriceTimeline

router = APIRouter(prefix="/prices", tags=["prices"])


@router.get(
    "/timeline",
    response_model=list[PriceTimeline],
    summary="Price evolution timeline",
    description="Returns median prices by arrondissement and year (2014-2023). Use for the timeline slider.",
)
def get_price_timeline(
    arrondissement: int | None = Query(None, ge=1, le=20, description="Optional: filter by arrondissement")
):
    """Fetch price timeline data."""
    df = load_gold_table("price_timeline.parquet")
    if df.empty:
        raise HTTPException(status_code=503, detail="Price timeline data not available")

    if arrondissement:
        df = df[df["arrondissement"] == arrondissement]
        if df.empty:
            raise HTTPException(status_code=404, detail=f"No data for arrondissement {arrondissement}")

    # Sort by year
    df = df.sort_values(["arrondissement", "year"])

    results = []
    for _, row in df.iterrows():
        results.append(PriceTimeline(
            arrondissement=int(row["arrondissement"]),
            year=int(row["year"]),
            median_price=row.get("median_price"),
            transaction_count=int(row.get("transaction_count", 0)),
        ))
    return results


@router.get(
    "/arrondissement/{arrondissement}",
    response_model=list[PriceTimeline],
    summary="Price history for one arrondissement",
    description="Get historical median prices for a specific arrondissement (2014-2023).",
)
def get_arrondissement_price_history(arrondissement: int = Query(..., ge=1, le=20)):
    """Fetch price history for a specific arrondissement."""
    df = load_gold_table("price_timeline.parquet")
    if df.empty:
        raise HTTPException(status_code=503, detail="Price timeline data not available")

    df = df[df["arrondissement"] == arrondissement].sort_values("year")
    if df.empty:
        raise HTTPException(status_code=404, detail=f"No price data for arrondissement {arrondissement}")

    results = []
    for _, row in df.iterrows():
        results.append(PriceTimeline(
            arrondissement=int(row["arrondissement"]),
            year=int(row["year"]),
            median_price=row.get("median_price"),
            transaction_count=int(row.get("transaction_count", 0)),
        ))
    return results
