"""
/api/scores/* — Scores de vivabilité par arrondissement.
Source : PostgreSQL table gold_arrondissement_summary (+ gold_indicator_scores).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.dependencies import get_db
from api.schemas import ArrondissementDetail, ArrondissementScore

router = APIRouter(prefix="/scores", tags=["scores"])

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
    COALESCE(bar_count,        0)::int  AS bar_count,
    COALESCE(nb_nightclubs,    0)::int  AS nightclub_count,
    COALESCE(park_count,       0)::int  AS park_count,
    median_price,
    NULL::real                          AS social_housing_pct
"""


def _row_to_score(row) -> ArrondissementScore:
    return ArrondissementScore(
        arrondissement=int(row["arrondissement"]),
        anime_score=float(row["anime_score"] or 0),
        calme_score=float(row["calme_score"] or 0),
        accessibilite_score=float(row["accessibilite_score"] or 0),
        connectivity_score=float(row["connectivity_score"]) if row["connectivity_score"] is not None else None,
        mobility_score=float(row["mobility_score"])         if row["mobility_score"]     is not None else None,
        health_env_score=float(row["health_env_score"])     if row["health_env_score"]   is not None else None,
        tranquility_score=float(row["tranquility_score"])   if row["tranquility_score"]  is not None else None,
        livability_score=float(row["livability_score"])     if row["livability_score"]   is not None else None,
        bar_count=int(row["bar_count"] or 0),
        nightclub_count=int(row["nightclub_count"] or 0),
        park_count=int(row["park_count"] or 0),
        median_price=float(row["median_price"]) if row["median_price"] is not None else None,
        social_housing_pct=float(row["social_housing_pct"]) if row["social_housing_pct"] is not None else None,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/all",
    response_model=list[ArrondissementScore],
    summary="Scores de vivabilité — 20 arrondissements",
    description=(
        "Retourne les 7 scores (Animé, Calme, Accessibilité, Connectivité, "
        "Mobilité, Santé Environnementale, Tranquillité) pour les 20 arrondissements."
    ),
)
def get_all_scores(db: Session = Depends(get_db)) -> list[ArrondissementScore]:
    try:
        rows = db.execute(
            text(f"SELECT {_SCORE_COLS} FROM gold_arrondissement_summary ORDER BY arrondissement")
        ).mappings().all()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Erreur base de données : {exc}")

    if not rows:
        raise HTTPException(status_code=503, detail="Table gold_arrondissement_summary vide ou absente")

    return [_row_to_score(row) for row in rows]


@router.get(
    "/indicators/all",
    response_model=list[ArrondissementDetail],
    summary="Scores stratégiques détaillés avec géométrie",
    description=(
        "Retourne les 4 nouveaux scores + géométrie WKT pour le rendu "
        "choroplèthe (MapLibre). Source : gold_indicator_scores."
    ),
)
def get_indicator_scores(db: Session = Depends(get_db)) -> list[ArrondissementDetail]:
    try:
        rows = db.execute(
            text("""
                SELECT
                    s.arrondissement,
                    s.nom_arrondissement,
                    s.geometry_wkt,
                    s.anime_score,
                    s.calme_score,
                    s.accessibilite_score,
                    s.connectivity_score,
                    s.mobility_score,
                    s.health_env_score,
                    s.tranquility_score,
                    s.livability_score,
                    -- métriques brutes depuis la table maîtresse
                    m.pct_eligible_ftth,
                    m.pct_pop_4g_mean,
                    m.pct_t2_t3,
                    m.station_count_velib,
                    m.avg_bikes_available,
                    m.nb_ilots_fraicheur,
                    m.surface_fraicheur_ha,
                    m.arbres_per_km2,
                    m.crime_count_total,
                    m.crime_rate_per_1000,
                    m.noise_lden_surface_ha,
                    m.nb_bars,
                    m.nb_nightclubs,
                    m.median_price
                FROM gold_indicator_scores s
                LEFT JOIN gold_arrondissement_summary m USING (arrondissement)
                ORDER BY s.arrondissement
            """)
        ).mappings().all()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Erreur base de données : {exc}")

    if not rows:
        raise HTTPException(status_code=503, detail="Table gold_indicator_scores vide ou absente")

    results = []
    for row in rows:
        results.append(ArrondissementDetail(
            arrondissement=int(row["arrondissement"]),
            nom_arrondissement=str(row["nom_arrondissement"] or ""),
            geometry_wkt=row["geometry_wkt"],
            anime_score=_safe_float(row, "anime_score"),
            calme_score=_safe_float(row, "calme_score"),
            accessibilite_score=_safe_float(row, "accessibilite_score"),
            connectivity_score=_safe_float(row, "connectivity_score"),
            mobility_score=_safe_float(row, "mobility_score"),
            health_env_score=_safe_float(row, "health_env_score"),
            tranquility_score=_safe_float(row, "tranquility_score"),
            livability_score=_safe_float(row, "livability_score"),
            pct_eligible_ftth=_safe_float(row, "pct_eligible_ftth"),
            pct_pop_4g_mean=_safe_float(row, "pct_pop_4g_mean"),
            pct_t2_t3=_safe_float(row, "pct_t2_t3"),
            station_count_velib=_safe_int(row, "station_count_velib"),
            avg_bikes_available=_safe_float(row, "avg_bikes_available"),
            nb_ilots_fraicheur=_safe_int(row, "nb_ilots_fraicheur"),
            surface_fraicheur_ha=_safe_float(row, "surface_fraicheur_ha"),
            arbres_per_km2=_safe_float(row, "arbres_per_km2"),
            crime_count_total=_safe_int(row, "crime_count_total"),
            crime_rate_per_1000=_safe_float(row, "crime_rate_per_1000"),
            noise_lden_surface_ha=_safe_float(row, "noise_lden_surface_ha"),
            nb_bars=_safe_int(row, "nb_bars"),
            nb_nightclubs=_safe_int(row, "nb_nightclubs"),
            median_price=_safe_float(row, "median_price"),
        ))
    return results


@router.get(
    "/{arrondissement}",
    response_model=ArrondissementScore,
    summary="Score d'un arrondissement",
    description="Scores détaillés pour un arrondissement spécifique (1-20).",
)
def get_arrondissement_score(
    arrondissement: int = Path(..., ge=1, le=20),
    db: Session = Depends(get_db),
) -> ArrondissementScore:
    try:
        row = db.execute(
            text(f"SELECT {_SCORE_COLS} FROM gold_arrondissement_summary WHERE arrondissement = :arr"),
            {"arr": arrondissement},
        ).mappings().first()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Erreur base de données : {exc}")

    if row is None:
        raise HTTPException(status_code=404, detail=f"Arrondissement {arrondissement} introuvable")

    return _row_to_score(row)


# ---------------------------------------------------------------------------
# Utilitaires internes
# ---------------------------------------------------------------------------

def _safe_float(row, col: str) -> float | None:
    v = row[col]
    return float(v) if v is not None else None


def _safe_int(row, col: str) -> int | None:
    v = row[col]
    return int(v) if v is not None else None
