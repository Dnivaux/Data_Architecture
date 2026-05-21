"""
/api/connectivity — Détail connectivité par opérateur
======================================================
Lit le Bronze arcep_mobile (Parquet) et retourne la couverture
4G/5G par opérateur pour un arrondissement donné.

Endpoint :
  GET /api/connectivity/{arrondissement}/operators
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Path as PathParam

router = APIRouter(prefix="/connectivity", tags=["connectivity"])
logger = logging.getLogger("api.connectivity")

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_BRONZE_MOBILE = _PROJECT_ROOT / "data" / "bronze" / "arcep_mobile"
_BRONZE_FIBRE  = _PROJECT_ROOT / "data" / "bronze" / "arcep_fibre"

_OPERATOR_LABELS = {
    "orange":   "Orange",
    "sfr":      "SFR",
    "bouygues": "Bouygues Telecom",
    "free":     "Free Mobile",
}


def _latest_parquet(bronze_dir: Path) -> Path | None:
    if not bronze_dir.exists():
        return None
    date_dirs = sorted(bronze_dir.glob("date=*"), reverse=True)
    for d in date_dirs:
        files = list(d.glob("part-*.parquet"))
        if files:
            return files[0]
    return None


@router.get(
    "/{arrondissement}/operators",
    summary="Couverture réseau par opérateur pour un arrondissement",
    description=(
        "Retourne la couverture 4G/5G de chaque opérateur (Orange, SFR, Bouygues, Free) "
        "et le taux d'éligibilité fibre pour l'arrondissement sélectionné."
    ),
)
def get_operator_detail(
    arrondissement: int = PathParam(..., ge=1, le=20),
) -> dict:
    try:
        import pandas as pd
    except ImportError:
        raise HTTPException(status_code=503, detail="pandas non installé")

    # --- Couverture mobile par opérateur ---
    operators: list[dict] = []
    mobile_file = _latest_parquet(_BRONZE_MOBILE)
    if mobile_file:
        try:
            df = pd.read_parquet(mobile_file)
            # Filtrer Paris et l'arrondissement
            commune_code = f"751{str(arrondissement).zfill(2)}"
            df_arr = df[df["commune_code"].astype(str).str.strip() == commune_code]

            if not df_arr.empty:
                for _, row in df_arr.iterrows():
                    op_raw = str(row.get("operateur", "")).lower().strip()
                    op_label = _OPERATOR_LABELS.get(op_raw, op_raw.capitalize())

                    pct_4g = row.get("pct_pop_4g")
                    pct_5g = row.get("pct_pop_5g")

                    operators.append({
                        "operateur":    op_raw,
                        "label":        op_label,
                        "has_4g":       bool(row.get("has_4g", False)),
                        "has_5g":       bool(row.get("has_5g", False)),
                        "pct_pop_4g":   round(float(pct_4g), 1) if pct_4g is not None and pd.notna(pct_4g) else None,
                        "pct_pop_5g":   round(float(pct_5g), 1) if pct_5g is not None and pd.notna(pct_5g) else None,
                        "periode":      str(row.get("periode", "")),
                    })
                logger.info("Opérateurs arr=%d : %d lignes", arrondissement, len(operators))
            else:
                logger.warning("Aucune donnée opérateur pour arrondissement %d", arrondissement)
        except Exception as exc:
            logger.error("Erreur lecture arcep_mobile : %s", exc)

    # --- Taux fibre FTTH ---
    ftth_pct: float | None = None
    fibre_file = _latest_parquet(_BRONZE_FIBRE)
    if fibre_file:
        try:
            df_fib = pd.read_parquet(fibre_file)
            commune_code = f"751{str(arrondissement).zfill(2)}"
            row_fib = df_fib[df_fib["commune_code"].astype(str).str.strip() == commune_code]
            if not row_fib.empty:
                val = row_fib["pct_eligible_ftth"].mean()
                ftth_pct = round(float(val), 1) if pd.notna(val) else None
        except Exception as exc:
            logger.error("Erreur lecture arcep_fibre : %s", exc)

    # Meilleur opérateur 4G (par pct_pop_4g)
    best_4g = None
    if operators:
        ops_with_4g = [o for o in operators if o["pct_pop_4g"] is not None]
        if ops_with_4g:
            best_4g = max(ops_with_4g, key=lambda o: o["pct_pop_4g"])["label"]

    # Meilleur opérateur 5G
    best_5g = None
    if operators:
        ops_with_5g = [o for o in operators if o["pct_pop_5g"] is not None]
        if ops_with_5g:
            best_5g = max(ops_with_5g, key=lambda o: o["pct_pop_5g"])["label"]

    if not operators and ftth_pct is None:
        raise HTTPException(
            status_code=404,
            detail=f"Données de connectivité indisponibles pour l'arrondissement {arrondissement}. Relancer le pipeline d'ingestion ARCEP.",
        )

    return {
        "arrondissement":  arrondissement,
        "ftth_pct":        ftth_pct,
        "best_4g":         best_4g,
        "best_5g":         best_5g,
        "operators":       operators,
    }
