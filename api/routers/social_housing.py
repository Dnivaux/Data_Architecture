"""
/api/social-housing/* — Évolution du parc de logements sociaux financés.
Source primaire : PostgreSQL gold_social_housing_timeline.
Fallback        : data/gold/social_housing_timeline.parquet (si PG hors ligne).

Répond à l'attendu consigne : « part des logements sociaux et son évolution ».
"""
from __future__ import annotations

from pathlib import Path as FilePath

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.dependencies import get_db
from api.schemas import SocialHousingPoint

router = APIRouter(prefix="/social-housing", tags=["social-housing"])

_GOLD_DIR = FilePath(__file__).parents[2] / "data" / "gold"


def _to_point(row) -> SocialHousingPoint:
    g = (lambda k, d=0: row[k] if (k in row and row[k] is not None) else d)
    return SocialHousingPoint(
        arrondissement=int(row["arrondissement"]),
        annee=int(row["annee"]),
        logements_finances=int(g("logements_finances")),
        logements_cumules=int(g("logements_cumules")),
    )


@router.get(
    "/timeline",
    response_model=list[SocialHousingPoint],
    summary="Évolution du parc social (logements financés par an)",
    description=(
        "Logements sociaux financés par arrondissement et par année, "
        "avec cumul. Filtre optionnel par arrondissement."
    ),
)
def get_social_housing_timeline(
    arrondissement: int | None = Query(None, ge=1, le=20),
    db: Session = Depends(get_db),
) -> list[SocialHousingPoint]:
    # --- Tentative PostgreSQL ---
    try:
        where = "WHERE arrondissement = :arr" if arrondissement else ""
        params = {"arr": arrondissement} if arrondissement else {}
        rows = db.execute(
            text(
                "SELECT arrondissement, annee, "
                "COALESCE(logements_finances, 0) AS logements_finances, "
                "COALESCE(logements_cumules, 0) AS logements_cumules "
                f"FROM gold_social_housing_timeline {where} "
                "ORDER BY arrondissement, annee"
            ),
            params,
        ).mappings().all()
        if rows:
            return [_to_point(dict(r)) for r in rows]
    except Exception:
        pass

    # --- Fallback Parquet ---
    path = _GOLD_DIR / "social_housing_timeline.parquet"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Timeline logements sociaux absente (pipeline Gold non exécuté ?)")
    df = pd.read_parquet(path, engine="pyarrow")
    if arrondissement:
        df = df[df["arrondissement"] == arrondissement]
    if df.empty:
        raise HTTPException(status_code=404, detail="Aucune donnée pour ce filtre")
    return [_to_point(row) for _, row in df.iterrows()]
