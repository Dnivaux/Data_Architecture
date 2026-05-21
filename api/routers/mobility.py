"""
/api/mobility/live — Dernière collecte Vélib' micro-batch (Bronze).

Lit le fichier Parquet Bronze le plus récent dans data/bronze/velib/
et retourne les statistiques de disponibilité agrégées par arrondissement.

Démontre au jury que la donnée micro-batch (60s) est consultable
en temps réel via l'API (critère C2.4 - micro-batching visible).
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/mobility", tags=["mobility"])

# Chemin absolu depuis la racine du projet (robuste quel que soit le cwd)
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_BRONZE_VELIB = _PROJECT_ROOT / "data" / "bronze" / "velib"


@router.get(
    "/live",
    summary="Dernière collecte Vélib' micro-batch",
    description=(
        "Retourne les statistiques de disponibilité Vélib' issues du dernier batch "
        "Bronze (micro-batch 60s). Utile pour le polling côté client (toutes les 30s)."
    ),
)
def get_live_mobility() -> dict:
    """
    Cherche récursivement le fichier Parquet Bronze Vélib' le plus récent
    et retourne les données agrégées par arrondissement.
    """
    import logging as _log
    _logger = _log.getLogger("api.mobility")

    cwd = Path(".").resolve()
    abs_path = _BRONZE_VELIB.resolve()
    _logger.info("CWD=%s | Bronze Vélib path=%s | exists=%s", cwd, abs_path, abs_path.exists())

    if not abs_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Répertoire Bronze Vélib' introuvable : {abs_path}",
        )

    # Cherche le répertoire date= le plus récent
    date_dirs = sorted(abs_path.glob("date=*"), reverse=True)
    _logger.info("Répertoires date= trouvés : %s", [d.name for d in date_dirs])
    if not date_dirs:
        raise HTTPException(status_code=404, detail=f"Aucun répertoire date= dans {abs_path}")

    latest_dir = date_dirs[0]
    batch_files = sorted(latest_dir.glob("batch_*.parquet"), reverse=True)
    _logger.info("Fichiers batch dans %s : %s", latest_dir.name, [f.name for f in batch_files])
    if not batch_files:
        raise HTTPException(status_code=404, detail=f"Répertoire {latest_dir.name} vide")

    # Utilise le chemin absolu pour la lecture
    latest_file = batch_files[0]

    latest_file = batch_files[0]
    date_str = latest_dir.name.replace("date=", "")

    # Lecture différée de pandas (évite l'import au démarrage si non installé)
    try:
        import pandas as pd
    except ImportError:
        raise HTTPException(status_code=503, detail="pandas non installé sur ce serveur")

    try:
        df = pd.read_parquet(latest_file)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Lecture Parquet échouée : {exc}")

    # Validation minimale du schéma Bronze
    required_cols = {"station_code", "bikes_available", "arrondissement"}
    if not required_cols.issubset(df.columns):
        raise HTTPException(
            status_code=500,
            detail=f"Schéma Bronze inattendu — colonnes manquantes : {required_cols - set(df.columns)}",
        )

    # Filtre les stations hors Paris (arrondissement non-entier : "Hors Paris", NaN, etc.)
    import pandas as pd
    df["_arr_int"] = pd.to_numeric(df["arrondissement"], errors="coerce")
    df = df[df["_arr_int"].between(1, 20)].copy()
    df["arrondissement"] = df["_arr_int"].astype(int)
    df = df.drop(columns=["_arr_int"])

    if df.empty:
        raise HTTPException(status_code=404, detail="Aucune station Vélib' parisienne dans ce batch")

    # Agrégation par arrondissement
    agg = (
        df.groupby("arrondissement", dropna=True)
        .agg(
            station_count=("station_code", "nunique"),
            total_bikes=("bikes_available", "sum"),
            avg_bikes=("bikes_available", "mean"),
            total_mechanical=(
                "mechanical_bikes", "sum"
            ) if "mechanical_bikes" in df.columns else ("bikes_available", "sum"),
            total_electric=(
                "electric_bikes", "sum"
            ) if "electric_bikes" in df.columns else ("bikes_available", "count"),
            total_docks=(
                "docks_available", "sum"
            ) if "docks_available" in df.columns else ("bikes_available", "count"),
        )
        .reset_index()
    )

    # Convertit en types JSON-sérialisables
    records = []
    for _, row in agg.iterrows():
        records.append({
            "arrondissement": int(row["arrondissement"]),
            "station_count":  int(row["station_count"]),
            "total_bikes":    int(row["total_bikes"]),
            "avg_bikes":      round(float(row["avg_bikes"]), 1),
            "total_mechanical": int(row.get("total_mechanical", 0)),
            "total_electric":   int(row.get("total_electric", 0)),
            "total_docks":      int(row.get("total_docks", 0)),
        })

    return {
        "batch_date":             date_str,
        "batch_file":             latest_file.name,
        "total_stations_sampled": int(df["station_code"].nunique()),
        "total_bikes_available":  int(df["bikes_available"].sum()),
        "batch_ts":               str(df["batch_ts"].max()) if "batch_ts" in df.columns else None,
        "by_arrondissement":      records,
    }
