"""
/api/comparison/* — Comparaison de deux arrondissements.
Source : PostgreSQL table gold_arrondissement_summary.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.dependencies import get_db
from api.routers.scores import _row_to_score
from api.schemas import ArrondissementComparison

router = APIRouter(prefix="/comparison", tags=["comparison"])

_SCORE_COLS = """
    arrondissement,
    COALESCE(anime_score,         0)::real  AS anime_score,
    COALESCE(calme_score,         0)::real  AS calme_score,
    COALESCE(accessibilite_score, 0)::real  AS accessibilite_score,
    connectivity_score,
    mobility_score,
    health_env_score,
    tranquility_score,
    livability_score,
    COALESCE(bar_count,       0)::int   AS bar_count,
    COALESCE(nightclub_count, 0)::int   AS nightclub_count,
    COALESCE(park_count,      0)::int   AS park_count,
    median_price,
    social_housing_pct
"""


@router.get(
    "/",
    response_model=ArrondissementComparison,
    summary="Comparer deux arrondissements",
    description=(
        "Comparaison côte-à-côte des 7 scores de vivabilité et du prix médian "
        "entre deux arrondissements parisiens."
    ),
)
def compare_arrondissements(
    a: int = Query(..., ge=1, le=20, description="Premier arrondissement"),
    b: int = Query(..., ge=1, le=20, description="Second arrondissement"),
    db: Session = Depends(get_db),
) -> ArrondissementComparison:
    if a == b:
        raise HTTPException(
            status_code=400,
            detail="Impossible de comparer un arrondissement avec lui-même",
        )

    try:
        rows = db.execute(
            text(f"""
                SELECT {_SCORE_COLS}
                FROM gold_arrondissement_summary
                WHERE arrondissement IN (:a, :b)
                ORDER BY arrondissement
            """),
            {"a": a, "b": b},
        ).mappings().all()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Erreur base de données : {exc}")

    row_map = {int(r["arrondissement"]): r for r in rows}

    if a not in row_map or b not in row_map:
        missing = [x for x in (a, b) if x not in row_map]
        raise HTTPException(
            status_code=404,
            detail=f"Arrondissement(s) introuvable(s) : {missing}",
        )

    score_a = _row_to_score(row_map[a])
    score_b = _row_to_score(row_map[b])

    # Différence de prix
    price_diff: float | None = None
    pa = row_map[a]["median_price"]
    pb = row_map[b]["median_price"]
    if pa is not None and pb is not None:
        price_diff = round(float(pb) - float(pa), 2)

    # Différence de vivabilité composite (livability_score en priorité, sinon moyenne des 3)
    lv_a = score_a.livability_score or (
        (score_a.anime_score + score_a.calme_score + score_a.accessibilite_score) / 3
    )
    lv_b = score_b.livability_score or (
        (score_b.anime_score + score_b.calme_score + score_b.accessibilite_score) / 3
    )
    livability_diff = round(lv_b - lv_a, 2)

    return ArrondissementComparison(
        arrond_a=a,
        arrond_b=b,
        scores_a=score_a,
        scores_b=score_b,
        price_diff=price_diff,
        livability_diff=livability_diff,
    )


@router.get(
    "/ranking",
    response_model=list[dict],
    summary="Classement des 20 arrondissements",
    description=(
        "Retourne le classement des arrondissements par livability_score décroissant. "
        "Inclut les 7 scores + rang."
    ),
)
def get_ranking(
    score_field: str = Query(
        "livability_score",
        description="Champ de tri : livability_score | anime_score | calme_score | "
                    "connectivity_score | mobility_score | health_env_score | tranquility_score",
    ),
    db: Session = Depends(get_db),
) -> list[dict]:
    _allowed = {
        "livability_score", "anime_score", "calme_score", "accessibilite_score",
        "connectivity_score", "mobility_score", "health_env_score", "tranquility_score",
    }
    if score_field not in _allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Champ de tri invalide. Valeurs acceptées : {sorted(_allowed)}",
        )

    try:
        rows = db.execute(text(f"""
            SELECT
                ROW_NUMBER() OVER (ORDER BY {score_field} DESC NULLS LAST) AS rang,
                arrondissement,
                nom_arrondissement,
                ROUND(COALESCE(livability_score,   0)::numeric, 1) AS livability_score,
                ROUND(COALESCE(anime_score,         0)::numeric, 1) AS anime_score,
                ROUND(COALESCE(calme_score,         0)::numeric, 1) AS calme_score,
                ROUND(COALESCE(accessibilite_score, 0)::numeric, 1) AS accessibilite_score,
                ROUND(COALESCE(connectivity_score,  0)::numeric, 1) AS connectivity_score,
                ROUND(COALESCE(mobility_score,      0)::numeric, 1) AS mobility_score,
                ROUND(COALESCE(health_env_score,    0)::numeric, 1) AS health_env_score,
                ROUND(COALESCE(tranquility_score,   0)::numeric, 1) AS tranquility_score,
                median_price
            FROM gold_indicator_scores
            ORDER BY {score_field} DESC NULLS LAST
        """)).mappings().all()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Erreur base de données : {exc}")

    return [dict(row) for row in rows]
