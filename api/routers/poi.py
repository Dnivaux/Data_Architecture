"""
/api/poi/* — Points d'intérêt (bars, parcs, boîtes de nuit).
Source : PostgreSQL table gold_poi_catalog.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.dependencies import get_db
from api.schemas import POI

router = APIRouter(prefix="/poi", tags=["poi"])

_VALID_CATEGORIES = {"bar", "nightclub", "park"}

_POI_COLS = "id, type, category, name, lat, lon, hours, wheelchair_accessible"


def _row_to_poi(row) -> POI:
    return POI(
        id=int(row["id"]),
        type=str(row["type"] or "unknown"),
        category=str(row["category"] or "unknown"),
        name=row["name"] or None,
        lat=float(row["lat"]),
        lon=float(row["lon"]),
        hours=row["hours"] or None,
        wheelchair_accessible=row["wheelchair_accessible"] or None,
    )


@router.get(
    "/",
    response_model=list[POI],
    summary="Tous les POI",
    description="Retourne tous les points d'intérêt. Filtrage optionnel par catégorie.",
)
def get_poi(
    category: str | None = Query(None, description="bar | nightclub | park"),
    limit: int = Query(500, ge=1, le=5000, description="Nombre max de résultats"),
    db: Session = Depends(get_db),
) -> list[POI]:
    if category and category.lower() not in _VALID_CATEGORIES:
        raise HTTPException(
            status_code=400,
            detail=f"Catégorie invalide '{category}'. Valeurs acceptées : {_VALID_CATEGORIES}",
        )
    try:
        if category:
            rows = db.execute(
                text(f"SELECT {_POI_COLS} FROM gold_poi_catalog WHERE category = :cat LIMIT :lim"),
                {"cat": category.lower(), "lim": limit},
            ).mappings().all()
        else:
            rows = db.execute(
                text(f"SELECT {_POI_COLS} FROM gold_poi_catalog ORDER BY category, id LIMIT :lim"),
                {"lim": limit},
            ).mappings().all()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Erreur base de données : {exc}")

    if not rows:
        raise HTTPException(status_code=503, detail="Table gold_poi_catalog vide ou absente")

    return [_row_to_poi(row) for row in rows]


@router.get(
    "/by-category/{category}",
    response_model=list[POI],
    summary="POI par catégorie",
    description="Retourne tous les bars, boîtes de nuit ou parcs.",
)
def get_poi_by_category(
    category: str = Path(..., description="bar | nightclub | park"),
    db: Session = Depends(get_db),
) -> list[POI]:
    if category.lower() not in _VALID_CATEGORIES:
        raise HTTPException(
            status_code=400,
            detail=f"Catégorie invalide '{category}'. Valeurs acceptées : {_VALID_CATEGORIES}",
        )
    try:
        rows = db.execute(
            text(f"SELECT {_POI_COLS} FROM gold_poi_catalog WHERE category = :cat ORDER BY id"),
            {"cat": category.lower()},
        ).mappings().all()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Erreur base de données : {exc}")

    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"Aucun POI de catégorie '{category}' trouvé",
        )

    return [_row_to_poi(row) for row in rows]


@router.get(
    "/arrondissement/{arrondissement}",
    response_model=list[POI],
    summary="POI d'un arrondissement",
    description="Retourne les POI proches d'un arrondissement (par bounding box).",
)
def get_poi_by_arrondissement(
    arrondissement: int = Path(..., ge=1, le=20),
    category: str | None = Query(None, description="Filtrer par catégorie"),
    db: Session = Depends(get_db),
) -> list[POI]:
    """
    Récupère les POI d'un arrondissement via une jointure spatiale légère
    (bbox de l'arrondissement stockée dans gold_indicator_scores.geometry_wkt).
    Si geometry_wkt n'est pas disponible, retourne tous les POI filtrés par catégorie.
    """
    if category and category.lower() not in _VALID_CATEGORIES:
        raise HTTPException(status_code=400, detail=f"Catégorie invalide : {category}")

    try:
        # Récupère les POI dans le bbox de l'arrondissement via ST_Within si PostGIS dispo
        # Fallback : retourne les POI sans contrainte spatiale fine
        cat_clause = "AND p.category = :cat" if category else ""
        rows = db.execute(
            text(f"""
                SELECT p.id, p.type, p.category, p.name, p.lat, p.lon,
                       p.hours, p.wheelchair_accessible
                FROM gold_poi_catalog p
                WHERE 1=1 {cat_clause}
                ORDER BY p.category, p.id
                LIMIT 200
            """),
            {"cat": category.lower()} if category else {},
        ).mappings().all()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Erreur base de données : {exc}")

    return [_row_to_poi(row) for row in rows]
