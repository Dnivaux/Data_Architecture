"""
/api/scores/* — Scores de vivabilité par arrondissement.
Source primaire  : PostgreSQL table gold_arrondissement_summary (+ gold_indicator_scores).
Fallback Parquet : data/gold/*.parquet (si PostgreSQL inaccessible).
"""
from __future__ import annotations

from pathlib import Path as FilePath

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.dependencies import get_db
from api.schemas import ArrondissementDetail, ArrondissementScore

# ---------------------------------------------------------------------------
# Fallback Parquet (si PostgreSQL inaccessible)
# ---------------------------------------------------------------------------

_GOLD_DIR = FilePath(__file__).parents[2] / "data" / "gold"


def _read_gold_parquet(filename: str) -> pd.DataFrame:
    """Charge une table Gold Parquet. Retourne DataFrame vide si absente."""
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
    if v is None or (hasattr(v, '__class__') and v.__class__.__name__ in ('NAType', 'NaTType')):
        return None
    try:
        import math
        if isinstance(v, float) and math.isnan(v):
            return None
        return cast(v)
    except (TypeError, ValueError):
        return None


def _build_detail_from_row(row) -> ArrondissementDetail:
    """Construit un ArrondissementDetail depuis un dict-like (mapping SQL ou Series)."""
    return ArrondissementDetail(
        arrondissement=int(row["arrondissement"]),
        nom_arrondissement=str(row.get("nom_arrondissement", f"Paris {row['arrondissement']}e") or ""),
        geometry_wkt=row.get("geometry_wkt"),
        anime_score=_safe(row, "anime_score"),
        calme_score=_safe(row, "calme_score"),
        connectivity_score=_safe(row, "connectivity_score"),
        mobility_score=_safe(row, "mobility_score"),
        health_env_score=_safe(row, "health_env_score"),
        tranquility_score=_safe(row, "tranquility_score"),
        livability_score=_safe(row, "livability_score"),
        pct_eligible_ftth=_safe(row, "pct_eligible_ftth"),
        pct_pop_4g_mean=_safe(row, "pct_pop_4g_mean"),
        pct_t2_t3=_safe(row, "pct_t2_t3"),
        station_count_velib=_safe(row, "station_count_velib", int),
        avg_bikes_available=_safe(row, "avg_bikes_available"),
        transit_stop_count=_safe(row, "transit_stop_count", int),
        metro_count=_safe(row, "metro_count", int),
        rer_count=_safe(row, "rer_count", int),
        tram_count=_safe(row, "tram_count", int),
        bus_count=_safe(row, "bus_count", int),
        nb_ilots_fraicheur=_safe(row, "nb_ilots_fraicheur", int),
        surface_fraicheur_ha=_safe(row, "surface_fraicheur_ha"),
        arbres_per_km2=_safe(row, "arbres_per_km2"),
        european_aqi=_safe(row, "european_aqi"),
        pollen_total=_safe(row, "pollen_total"),
        pollen_risk=(row.get("pollen_risk") if hasattr(row, "get") else None),
        crime_count_total=_safe(row, "crime_count_total", int),
        crime_rate_per_1000=_safe(row, "crime_rate_per_1000"),
        noise_lden_surface_ha=_safe(row, "noise_lden_surface_ha"),
        nb_bars=_safe(row, "nb_bars", int),
        nb_nightclubs=_safe(row, "nb_nightclubs", int),
        cinema_count=_safe(row, "cinema_count", int),
        restaurant_count=_safe(row, "restaurant_count", int),
        stadium_count=_safe(row, "stadium_count", int),
        museum_count=_safe(row, "museum_count", int),
        median_price=_safe(row, "median_price"),
        nombre_logements_sociaux=_safe(row, "nombre_logements_sociaux", int),
    )


def _build_score_from_row(row) -> ArrondissementScore:
    """Construit un ArrondissementScore depuis un dict-like."""
    return ArrondissementScore(
        arrondissement=int(row["arrondissement"]),
        anime_score=float(_safe(row, "anime_score") or 0),
        calme_score=float(_safe(row, "calme_score") or 0),
        connectivity_score=_safe(row, "connectivity_score"),
        mobility_score=_safe(row, "mobility_score"),
        health_env_score=_safe(row, "health_env_score"),
        tranquility_score=_safe(row, "tranquility_score"),
        livability_score=_safe(row, "livability_score"),
        bar_count=int(_safe(row, "bar_count", int) or 0),
        nightclub_count=int(_safe(row, "nb_nightclubs", int) or 0),
        park_count=int(_safe(row, "park_count", int) or 0),
        cinema_count=int(_safe(row, "cinema_count", int) or 0),
        restaurant_count=int(_safe(row, "restaurant_count", int) or 0),
        stadium_count=int(_safe(row, "stadium_count", int) or 0),
        museum_count=int(_safe(row, "museum_count", int) or 0),
        median_price=_safe(row, "median_price"),
        social_housing_pct=None,
        nombre_logements_sociaux=_safe(row, "nombre_logements_sociaux", int),
    )

router = APIRouter(prefix="/scores", tags=["scores"])

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/all",
    response_model=list[ArrondissementScore],
    summary="Scores de vivabilité — 20 arrondissements",
    description=(
        "Retourne les 7 scores pour les 20 arrondissements. "
        "Source primaire : PostgreSQL. Fallback : Gold Parquet."
    ),
)
def get_all_scores(db: Session = Depends(get_db)) -> list[ArrondissementScore]:
    # --- Tentative PostgreSQL ---
    try:
        rows = db.execute(
            text("""
                SELECT arrondissement,
                    COALESCE(anime_score,         0)::real AS anime_score,
                    COALESCE(calme_score,         0)::real AS calme_score,
                    connectivity_score, mobility_score, health_env_score,
                    tranquility_score,  livability_score,
                    COALESCE(bar_count,        0)::int AS bar_count,
                    COALESCE(nb_nightclubs,    0)::int AS nb_nightclubs,
                    COALESCE(park_count,       0)::int AS park_count,
                    COALESCE(cinema_count,     0)::int AS cinema_count,
                    COALESCE(restaurant_count, 0)::int AS restaurant_count,
                    COALESCE(stadium_count,    0)::int AS stadium_count,
                    COALESCE(museum_count,     0)::int AS museum_count,
                    median_price, nombre_logements_sociaux
                FROM gold_arrondissement_summary ORDER BY arrondissement
            """)
        ).mappings().all()
        if rows:
            return [_build_score_from_row(dict(r)) for r in rows]
    except Exception:
        pass

    # --- Fallback Parquet ---
    df = _read_gold_parquet("arrondissement_summary.parquet")
    if df.empty:
        raise HTTPException(status_code=503, detail="Données indisponibles (PG hors ligne + Parquet absent)")
    return [_build_score_from_row(row) for _, row in df.iterrows()]


@router.get(
    "/indicators/all",
    response_model=list[ArrondissementDetail],
    summary="Scores stratégiques détaillés avec géométrie",
    description=(
        "Retourne les scores + métriques brutes + géométrie WKT. "
        "Source primaire : PostgreSQL. Fallback : Gold Parquet."
    ),
)
def get_indicator_scores(db: Session = Depends(get_db)) -> list[ArrondissementDetail]:
    # --- Tentative PostgreSQL ---
    try:
        rows = db.execute(
            text("""
                SELECT
                    s.arrondissement, s.nom_arrondissement, s.geometry_wkt,
                    s.anime_score, s.calme_score,
                    s.connectivity_score, s.mobility_score, s.health_env_score,
                    s.tranquility_score,  s.livability_score,
                    m.pct_eligible_ftth,   m.pct_pop_4g_mean, m.pct_t2_t3,
                    m.station_count_velib, m.avg_bikes_available,
                    m.transit_stop_count, m.metro_count, m.rer_count, m.tram_count, m.bus_count,
                    m.nb_ilots_fraicheur,  m.surface_fraicheur_ha, m.arbres_per_km2,
                    m.european_aqi, m.pollen_total, m.pollen_risk,
                    m.crime_count_total,   m.crime_rate_per_1000, m.noise_lden_surface_ha,
                    m.nb_bars, m.nb_nightclubs,
                    m.cinema_count, m.restaurant_count, m.stadium_count, m.museum_count,
                    m.median_price,
                    m.nombre_logements_sociaux
                FROM gold_indicator_scores s
                LEFT JOIN gold_arrondissement_summary m USING (arrondissement)
                ORDER BY s.arrondissement
            """)
        ).mappings().all()
        if rows:
            return [_build_detail_from_row(dict(r)) for r in rows]
    except Exception:
        pass

    # --- Fallback Parquet ---
    df = _read_gold_parquet("arrondissement_summary.parquet")
    if df.empty:
        raise HTTPException(status_code=503, detail="Données indisponibles (PG hors ligne + Parquet absent)")
    return [_build_detail_from_row(row) for _, row in df.iterrows()]


@router.get(
    "/{arrondissement}",
    response_model=ArrondissementScore,
    summary="Score d'un arrondissement",
    description="Scores détaillés pour un arrondissement spécifique (1-20). Fallback Parquet si PG hors ligne.",
)
def get_arrondissement_score(
    arrondissement: int = Path(..., ge=1, le=20),
    db: Session = Depends(get_db),
) -> ArrondissementScore:
    # --- Tentative PostgreSQL ---
    try:
        row = db.execute(
            text("""
                SELECT arrondissement,
                    COALESCE(anime_score,         0)::real AS anime_score,
                    COALESCE(calme_score,         0)::real AS calme_score,
                    connectivity_score, mobility_score, health_env_score,
                    tranquility_score,  livability_score,
                    COALESCE(bar_count,        0)::int AS bar_count,
                    COALESCE(nb_nightclubs,    0)::int AS nb_nightclubs,
                    COALESCE(park_count,       0)::int AS park_count,
                    COALESCE(cinema_count,     0)::int AS cinema_count,
                    COALESCE(restaurant_count, 0)::int AS restaurant_count,
                    COALESCE(stadium_count,    0)::int AS stadium_count,
                    COALESCE(museum_count,     0)::int AS museum_count,
                    median_price
                FROM gold_arrondissement_summary WHERE arrondissement = :arr
            """),
            {"arr": arrondissement},
        ).mappings().first()
        if row:
            return _build_score_from_row(dict(row))
    except Exception:
        pass

    # --- Fallback Parquet ---
    df = _read_gold_parquet("arrondissement_summary.parquet")
    if df.empty:
        raise HTTPException(status_code=503, detail="Données indisponibles (PG hors ligne + Parquet absent)")
    row_df = df[df["arrondissement"] == arrondissement]
    if row_df.empty:
        raise HTTPException(status_code=404, detail=f"Arrondissement {arrondissement} introuvable")
    return _build_score_from_row(row_df.iloc[0])


# Alias de compatibilité (utilisé par comparison.py)
_row_to_score = _build_score_from_row
