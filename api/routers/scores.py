"""
/api/scores/* routes – Livability scores by arrondissement.
"""
from fastapi import APIRouter, HTTPException, Query

from api.dependencies import load_gold_table
from api.schemas import ArrondissementScore

router = APIRouter(prefix="/scores", tags=["scores"])


@router.get(
    "/all",
    response_model=list[ArrondissementScore],
    summary="All livability scores",
    description="Returns 'Animé', 'Calme', and 'Accessibilité financière' scores for all 20 arrondissements.",
)
def get_all_scores():
    """Fetch livability scores for all arrondissements."""
    df = load_gold_table("arrondissement_summary.parquet")
    if df.empty:
        raise HTTPException(status_code=503, detail="Score data not available")

    results = []
    for _, row in df.iterrows():
        results.append(ArrondissementScore(
            arrondissement=int(row["arrondissement"]),
            anime_score=float(row.get("anime_score", 0)),
            calme_score=float(row.get("calme_score", 0)),
            accessibilite_score=float(row.get("accessibilite_score", 0)),
            bar_count=int(row.get("bar_count", 0)),
            nightclub_count=int(row.get("nightclub_count", 0)),
            park_count=int(row.get("park_count", 0)),
            median_price=row.get("median_price"),
            social_housing_pct=row.get("social_housing_pct"),
        ))
    return results


@router.get(
    "/{arrondissement}",
    response_model=ArrondissementScore,
    summary="Score for one arrondissement",
    description="Get detailed livability scores and stats for a specific arrondissement (1-20).",
)
def get_arrondissement_score(arrondissement: int = Query(..., ge=1, le=20)):
    """Fetch livability scores for a specific arrondissement."""
    df = load_gold_table("arrondissement_summary.parquet")
    if df.empty:
        raise HTTPException(status_code=503, detail="Score data not available")

    row = df[df["arrondissement"] == arrondissement]
    if row.empty:
        raise HTTPException(status_code=404, detail=f"Arrondissement {arrondissement} not found")

    row = row.iloc[0]
    return ArrondissementScore(
        arrondissement=int(row["arrondissement"]),
        anime_score=float(row.get("anime_score", 0)),
        calme_score=float(row.get("calme_score", 0)),
        accessibilite_score=float(row.get("accessibilite_score", 0)),
        bar_count=int(row.get("bar_count", 0)),
        nightclub_count=int(row.get("nightclub_count", 0)),
        park_count=int(row.get("park_count", 0)),
        median_price=row.get("median_price"),
        social_housing_pct=row.get("social_housing_pct"),
    )
