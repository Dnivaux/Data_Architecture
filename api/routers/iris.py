"""
/api/iris/* — Scores de vivabilité à la maille IRIS (grain primaire ~992 zones).
Source primaire  : PostgreSQL table gold_iris_indicator_scores (+ gold_iris_summary).
Fallback Parquet : data/gold/iris_*.parquet (si PostgreSQL inaccessible).

L'IRIS rend visible le détail *à l'intérieur* d'un arrondissement (l'arrondissement
reste exposé comme dimension parente via la colonne `arrondissement`).
"""
from __future__ import annotations

from pathlib import Path as FilePath

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.dependencies import get_db
from api.schemas import IrisDetail

# ---------------------------------------------------------------------------
# Fallback Parquet (si PostgreSQL inaccessible)
# ---------------------------------------------------------------------------

_GOLD_DIR = FilePath(__file__).parents[2] / "data" / "gold"


def _read_gold_parquet(filename: str) -> pd.DataFrame:
    path = _GOLD_DIR / filename
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_parquet(path, engine="pyarrow")
    except Exception:
        return pd.DataFrame()


def _safe(row, col, cast=float):
    """Extrait une valeur d'un dict ou d'une Series pandas, avec cast sécurisé."""
    v = row[col] if isinstance(row, dict) else row.get(col, None)
    if v is None or (hasattr(v, "__class__") and v.__class__.__name__ in ("NAType", "NaTType")):
        return None
    try:
        import math
        if isinstance(v, float) and math.isnan(v):
            return None
        return cast(v)
    except (TypeError, ValueError):
        return None


def _build_detail_from_row(row) -> IrisDetail:
    """Construit un IrisDetail depuis un dict-like (mapping SQL ou Series pandas)."""
    return IrisDetail(
        code_iris=str(row["code_iris"]),
        arrondissement=_safe(row, "arrondissement", int),
        nom_iris=(row.get("nom_iris") if hasattr(row, "get") else None),
        geometry_wkt=(row.get("geometry_wkt") if hasattr(row, "get") else None),
        anime_score=_safe(row, "anime_score"),
        connectivity_score=_safe(row, "connectivity_score"),
        mobility_score=_safe(row, "mobility_score"),
        health_env_score=_safe(row, "health_env_score"),
        tranquility_score=_safe(row, "tranquility_score"),
        livability_score=_safe(row, "livability_score"),
        median_price=_safe(row, "median_price"),
        median_income=_safe(row, "median_income"),
        gini_coefficient=_safe(row, "gini_coefficient"),
        poverty_rate=_safe(row, "poverty_rate"),
    )


router = APIRouter(prefix="/iris", tags=["iris"])

# Colonnes communes (PG + Parquet) pour les vues IRIS
_IRIS_COLS = (
    "code_iris, arrondissement, nom_iris, geometry_wkt, "
    "anime_score, connectivity_score, mobility_score, health_env_score, "
    "tranquility_score, livability_score, "
    "median_price, median_income, gini_coefficient, poverty_rate"
)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/indicators/all",
    response_model=list[IrisDetail],
    summary="Scores IRIS détaillés avec géométrie (~992 zones)",
    description=(
        "Retourne tous les IRIS parisiens : scores + métriques brutes + géométrie WKT. "
        "Idéal pour la choroplèthe infra-arrondissement. "
        "Source primaire : PostgreSQL. Fallback : Gold Parquet."
    ),
)
def get_iris_indicators(
    arrondissement: int | None = Query(None, ge=1, le=20, description="Filtrer sur un arrondissement parent"),
    db: Session = Depends(get_db),
) -> list[IrisDetail]:
    # --- Tentative PostgreSQL ---
    try:
        sql = f"SELECT {_IRIS_COLS} FROM gold_iris_indicator_scores"
        params: dict = {}
        if arrondissement is not None:
            sql += " WHERE arrondissement = :arr"
            params["arr"] = arrondissement
        sql += " ORDER BY code_iris"
        rows = db.execute(text(sql), params).mappings().all()
        if rows:
            return [_build_detail_from_row(dict(r)) for r in rows]
    except Exception:
        pass

    # --- Fallback Parquet ---
    df = _read_gold_parquet("iris_indicator_scores.parquet")
    if df.empty:
        df = _read_gold_parquet("iris_summary.parquet")
    if df.empty:
        raise HTTPException(status_code=503, detail="Données IRIS indisponibles (PG hors ligne + Parquet absent)")
    if arrondissement is not None:
        df = df[df["arrondissement"] == arrondissement]
    return [_build_detail_from_row(row) for _, row in df.iterrows()]


@router.get(
    "/all",
    response_model=list[IrisDetail],
    summary="Scores IRIS (toutes zones, sans géométrie lourde)",
    description="Identique à /indicators/all mais pensé pour les agrégations légères.",
)
def get_all_iris(db: Session = Depends(get_db)) -> list[IrisDetail]:
    return get_iris_indicators(arrondissement=None, db=db)


@router.get(
    "/{code_iris}",
    response_model=IrisDetail,
    summary="Détail d'un IRIS",
    description="Scores et métriques d'un IRIS précis (code INSEE 9 chiffres). Fallback Parquet si PG hors ligne.",
)
def get_iris(
    code_iris: str = Path(..., min_length=5, max_length=12, description="Code IRIS INSEE"),
    db: Session = Depends(get_db),
) -> IrisDetail:
    # --- Tentative PostgreSQL ---
    try:
        row = db.execute(
            text(f"SELECT {_IRIS_COLS} FROM gold_iris_indicator_scores WHERE code_iris = :code"),
            {"code": code_iris},
        ).mappings().first()
        if row:
            return _build_detail_from_row(dict(row))
    except Exception:
        pass

    # --- Fallback Parquet ---
    df = _read_gold_parquet("iris_indicator_scores.parquet")
    if df.empty:
        df = _read_gold_parquet("iris_summary.parquet")
    if df.empty:
        raise HTTPException(status_code=503, detail="Données IRIS indisponibles (PG hors ligne + Parquet absent)")
    row_df = df[df["code_iris"].astype(str) == code_iris]
    if row_df.empty:
        raise HTTPException(status_code=404, detail=f"IRIS {code_iris} introuvable")
    return _build_detail_from_row(row_df.iloc[0])
