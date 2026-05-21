"""
/api/prices/* — Série temporelle des prix immobiliers (DVF).
Source : PostgreSQL table gold_price_timeline.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy import text
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import Session

from api.dependencies import get_db
from api.schemas import PriceTimeline

router = APIRouter(prefix="/prices", tags=["prices"])

_PRICE_COLS = "arrondissement, year, median_price, COALESCE(transaction_count, 0) AS transaction_count"


def _row_to_price(row) -> PriceTimeline:
    return PriceTimeline(
        arrondissement=int(row["arrondissement"]),
        year=int(row["year"]),
        median_price=float(row["median_price"]) if row["median_price"] is not None else None,
        transaction_count=int(row["transaction_count"] or 0),
    )


def _handle_db_exc(exc: Exception, context: str) -> None:
    """Convertit les exceptions SQLAlchemy en HTTPException explicites."""
    msg = str(exc)
    if isinstance(exc, ProgrammingError) and (
        "does not exist" in msg or "relation" in msg or "column" in msg
    ):
        raise HTTPException(
            status_code=404,
            detail=f"Table ou colonne manquante ({context}) — pipeline Gold non exécuté ?",
        )
    if isinstance(exc, OperationalError):
        raise HTTPException(status_code=503, detail=f"PostgreSQL injoignable ({context})")
    raise HTTPException(status_code=500, detail=f"Erreur SQL inattendue ({context}) : {exc}")


@router.get(
    "/timeline",
    response_model=list[PriceTimeline],
    summary="Évolution des prix (tous arrondissements)",
    description=(
        "Retourne les prix médians DVF par arrondissement et par année. "
        "Filtre optionnel par arrondissement pour le slider temporel du dashboard."
    ),
)
def get_price_timeline(
    arrondissement: int | None = Query(None, ge=1, le=20, description="Filtrer par arrondissement"),
    year_min: int | None = Query(None, ge=2014, le=2030),
    year_max: int | None = Query(None, ge=2014, le=2030),
    db: Session = Depends(get_db),
) -> list[PriceTimeline]:
    try:
        conditions = ["1=1"]
        params: dict = {}

        if arrondissement:
            conditions.append("arrondissement = :arr")
            params["arr"] = arrondissement
        if year_min:
            conditions.append("year >= :ymin")
            params["ymin"] = year_min
        if year_max:
            conditions.append("year <= :ymax")
            params["ymax"] = year_max

        where = " AND ".join(conditions)
        rows = db.execute(
            text(f"SELECT {_PRICE_COLS} FROM gold_price_timeline WHERE {where} ORDER BY arrondissement, year"),
            params,
        ).mappings().all()
    except Exception as exc:
        _handle_db_exc(exc, "GET /prices/timeline")

    if not rows:
        detail = (
            f"Aucune donnée de prix pour l'arrondissement {arrondissement}"
            if arrondissement else "Table gold_price_timeline vide ou absente"
        )
        raise HTTPException(status_code=404, detail=detail)

    return [_row_to_price(row) for row in rows]


@router.get(
    "/arrondissement/{arrondissement}",
    response_model=list[PriceTimeline],
    summary="Historique des prix d'un arrondissement",
    description="Série temporelle complète pour un arrondissement spécifique (2014–présent).",
)
def get_arrondissement_price_history(
    arrondissement: int = Path(..., ge=1, le=20),
    db: Session = Depends(get_db),
) -> list[PriceTimeline]:
    try:
        rows = db.execute(
            text(f"""
                SELECT {_PRICE_COLS}
                FROM gold_price_timeline
                WHERE arrondissement = :arr
                ORDER BY year
            """),
            {"arr": arrondissement},
        ).mappings().all()
    except Exception as exc:
        _handle_db_exc(exc, f"GET /prices/arrondissement/{arrondissement}")

    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"Aucune donnée de prix pour l'arrondissement {arrondissement}",
        )

    return [_row_to_price(row) for row in rows]


@router.get(
    "/summary",
    response_model=list[dict],
    summary="Résumé statistique des prix par arrondissement",
    description="Min, max, moyenne et dernière valeur connue par arrondissement.",
)
def get_price_summary(db: Session = Depends(get_db)) -> list[dict]:
    try:
        rows = db.execute(text("""
            SELECT
                arrondissement,
                MIN(median_price)::real                 AS min_price,
                MAX(median_price)::real                 AS max_price,
                AVG(median_price)::real                 AS avg_price,
                MAX(year)                               AS latest_year,
                SUM(transaction_count)::int             AS total_transactions
            FROM gold_price_timeline
            WHERE median_price IS NOT NULL
            GROUP BY arrondissement
            ORDER BY arrondissement
        """)).mappings().all()
    except Exception as exc:
        _handle_db_exc(exc, "GET /prices/summary")

    return [dict(row) for row in rows]
