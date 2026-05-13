"""
/api/comparison/* routes – Compare two arrondissements.
"""
from fastapi import APIRouter, HTTPException, Query

from api.dependencies import load_gold_table
from api.schemas import ArrondissementComparison, ArrondissementScore

router = APIRouter(prefix="/comparison", tags=["comparison"])


@router.get(
    "/",
    response_model=ArrondissementComparison,
    summary="Compare two arrondissements",
    description="Side-by-side comparison of livability scores and prices between two arrondissements.",
)
def compare_arrondissements(
    a: int = Query(..., ge=1, le=20, description="First arrondissement"),
    b: int = Query(..., ge=1, le=20, description="Second arrondissement"),
):
    """Compare two arrondissements."""
    if a == b:
        raise HTTPException(status_code=400, detail="Cannot compare an arrondissement with itself")

    df = load_gold_table("arrondissement_summary.parquet")
    if df.empty:
        raise HTTPException(status_code=503, detail="Score data not available")

    row_a = df[df["arrondissement"] == a]
    row_b = df[df["arrondissement"] == b]

    if row_a.empty or row_b.empty:
        raise HTTPException(status_code=404, detail="One or both arrondissements not found")

    row_a = row_a.iloc[0]
    row_b = row_b.iloc[0]

    score_a = ArrondissementScore(
        arrondissement=int(row_a["arrondissement"]),
        anime_score=float(row_a.get("anime_score", 0)),
        calme_score=float(row_a.get("calme_score", 0)),
        accessibilite_score=float(row_a.get("accessibilite_score", 0)),
        bar_count=int(row_a.get("bar_count", 0)),
        nightclub_count=int(row_a.get("nightclub_count", 0)),
        park_count=int(row_a.get("park_count", 0)),
        median_price=row_a.get("median_price"),
        social_housing_pct=row_a.get("social_housing_pct"),
    )

    score_b = ArrondissementScore(
        arrondissement=int(row_b["arrondissement"]),
        anime_score=float(row_b.get("anime_score", 0)),
        calme_score=float(row_b.get("calme_score", 0)),
        accessibilite_score=float(row_b.get("accessibilite_score", 0)),
        bar_count=int(row_b.get("bar_count", 0)),
        nightclub_count=int(row_b.get("nightclub_count", 0)),
        park_count=int(row_b.get("park_count", 0)),
        median_price=row_b.get("median_price"),
        social_housing_pct=row_b.get("social_housing_pct"),
    )

    # Compute diffs
    price_diff = None
    if row_a.get("median_price") and row_b.get("median_price"):
        price_diff = float(row_b["median_price"]) - float(row_a["median_price"])

    livability_a = (float(row_a.get("anime_score", 0)) + float(row_a.get("calme_score", 0)) + float(row_a.get("accessibilite_score", 0))) / 3
    livability_b = (float(row_b.get("anime_score", 0)) + float(row_b.get("calme_score", 0)) + float(row_b.get("accessibilite_score", 0))) / 3
    livability_diff = livability_b - livability_a

    return ArrondissementComparison(
        arrond_a=a,
        arrond_b=b,
        scores_a=score_a,
        scores_b=score_b,
        price_diff=price_diff,
        livability_diff=livability_diff,
    )
