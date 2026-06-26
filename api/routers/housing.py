"""
/api/housing/* — Répartition du parc immobilier (typologie + surfaces).
Source : PostgreSQL table gold_housing_typology (fallback Parquet).

Répond à l'attendu consigne : « la répartition du parc immobilier selon les
types de logements et les surfaces ».
"""
from __future__ import annotations

from pathlib import Path as FilePath

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import Session

from api.dependencies import get_db
from api.schemas import HousingTypology

router = APIRouter(prefix="/housing", tags=["housing"])

_GOLD_DIR = FilePath(__file__).parents[2] / "data" / "gold"
_FIELDS = list(HousingTypology.model_fields.keys())


def _to_model(mapping) -> HousingTypology:
    """Construit le schéma depuis un dict-like (mapping SQL ou Series)."""
    def _g(col):
        v = mapping[col] if isinstance(mapping, dict) else mapping.get(col)
        if v is None or (hasattr(v, "__class__") and v.__class__.__name__ == "NAType"):
            return None
        try:
            import math
            if isinstance(v, float) and math.isnan(v):
                return None
        except TypeError:
            pass
        return v

    payload = {f: _g(f) for f in _FIELDS}
    # Valeurs par défaut pour les champs non nullables absents
    for f, v in list(payload.items()):
        if v is None and f != "median_surface" and f != "mean_surface":
            payload[f] = 0
    return HousingTypology(**payload)


@router.get(
    "/typology",
    response_model=HousingTypology,
    summary="Répartition du parc immobilier (typologie T1..T5+, type, surfaces)",
    description=(
        "Répartition du parc transigé (DVF) par typologie, type de bien et "
        "tranche de surface. arrondissement=0 retourne l'agrégat Paris entier."
    ),
)
def get_typology(
    arrondissement: int = Query(0, ge=0, le=20, description="0 = Paris entier"),
    db: Session = Depends(get_db),
) -> HousingTypology:
    # --- Tentative PostgreSQL ---
    try:
        row = db.execute(
            text("SELECT * FROM gold_housing_typology WHERE arrondissement = :arr"),
            {"arr": arrondissement},
        ).mappings().first()
        if row:
            return _to_model(dict(row))
    except (OperationalError, ProgrammingError):
        pass  # repli Parquet ci-dessous
    except Exception:
        pass

    # --- Fallback Parquet ---
    path = _GOLD_DIR / "housing_typology.parquet"
    if path.exists():
        df = pd.read_parquet(path, engine="pyarrow")
        sub = df[df["arrondissement"] == arrondissement]
        if not sub.empty:
            return _to_model(sub.iloc[0])

    raise HTTPException(
        status_code=404,
        detail=f"Aucune donnée de typologie pour l'arrondissement {arrondissement} "
               "(pipeline Gold non exécuté ?)",
    )
