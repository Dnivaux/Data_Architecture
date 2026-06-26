"""
/api/live/* — Diffusion temps réel (WebSocket) de la mobilité Vélib'.
=====================================================================
Répond au critère RNCP C2.2 : « La solution proposée permet de traiter (en
temps réel) et d'analyser l'ensemble de données au fur et à mesure de leur
disponibilité ».

Architecture
------------
  Le daemon micro-batch (src/ingestion/mobility_micro_batch.py) écrit en continu
  des lots Parquet :  data/bronze/velib/date=YYYY-MM-DD/batch_HH-MM-SS.parquet
  Ce routeur **observe** le dernier lot et **pousse** les agrégats aux clients
  WebSocket dès qu'un nouveau lot apparaît → livraison « au fil de l'eau ».

Endpoints
---------
  WS  /api/live/velib?api_key=...   flux temps réel (push à chaque nouveau lot)
  GET /api/live/velib/latest        dernier instantané agrégé (one-shot)

Lancer le producteur de données :
  python pipeline.py --mobility-daemon      (ou)   python -m src.ingestion.mobility_micro_batch
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from api.security import API_KEYS, is_valid_api_key, require_api_key

router = APIRouter(prefix="/live", tags=["live"])

_VELIB_ROOT = Path(__file__).parents[2] / "data" / "bronze" / "velib"
_AIR_ROOT = Path(__file__).parents[2] / "data" / "bronze" / "air_quality_live"
_POLL_SECONDS = 5  # fréquence de vérification d'un nouveau lot


def _latest_batch_in(root: Path) -> Path | None:
    """Retourne le chemin du lot le plus récent sous `root`, ou None."""
    if not root.exists():
        return None
    batches = sorted(root.rglob("batch_*.parquet"), key=lambda p: p.stat().st_mtime)
    return batches[-1] if batches else None


def _latest_batch_path() -> Path | None:
    """Retourne le chemin du lot Vélib' le plus récent, ou None."""
    return _latest_batch_in(_VELIB_ROOT)


def _aggregate_air(path: Path) -> dict:
    """Agrège un lot qualité de l'air en métriques temps réel exploitables."""
    df = pd.read_parquet(path, engine="pyarrow")
    cols = ["arrondissement", "european_aqi", "aqi_level", "aqi_label",
            "pm2_5", "pm10", "no2", "o3", "pollen_total", "pollen_risk"]
    by_arr = df[[c for c in cols if c in df.columns]].to_dict("records")
    aqi = df["european_aqi"].dropna()
    worst_idx = aqi.idxmax() if not aqi.empty else None
    return {
        "batch_file": path.name,
        "batch_ts": str(df["ingested_at"].max()) if "ingested_at" in df.columns else None,
        "served_at": datetime.now(timezone.utc).isoformat(),
        "totals": {
            "arrondissements": int(len(df)),
            "european_aqi_mean": round(float(aqi.mean()), 1) if not aqi.empty else None,
            "pm2_5_mean": round(float(df["pm2_5"].mean()), 1) if "pm2_5" in df.columns else None,
            "worst_arrondissement": int(df.loc[worst_idx, "arrondissement"]) if worst_idx is not None else None,
            "worst_aqi": round(float(aqi.max()), 1) if not aqi.empty else None,
        },
        "by_arrondissement": by_arr,
    }


@router.get(
    "/air/latest",
    summary="Dernier instantané qualité de l'air agrégé (temps réel)",
    dependencies=[Depends(require_api_key)],
)
def air_latest() -> dict:
    path = _latest_batch_in(_AIR_ROOT)
    if not path:
        return {"status": "no_data", "message": "Aucun lot air — lancer le daemon micro-batch air."}
    return {"status": "ok", **_aggregate_air(path)}


def _aggregate(path: Path) -> dict:
    """Agrège un lot Vélib' en métriques temps réel exploitables."""
    df = pd.read_parquet(path, engine="pyarrow")
    by_arr = (
        df.groupby("arrondissement")
        .agg(stations=("station_code", "count"),
             bikes=("bikes_available", "sum"),
             ebikes=("electric_bikes", "sum"),
             docks=("docks_available", "sum"))
        .reset_index()
        .to_dict("records")
    )
    return {
        "batch_file": path.name,
        "batch_ts": str(df["batch_ts"].max()) if "batch_ts" in df.columns else None,
        "served_at": datetime.now(timezone.utc).isoformat(),
        "totals": {
            "stations": int(df["station_code"].nunique()),
            "bikes_available": int(df["bikes_available"].sum()),
            "electric_bikes": int(df["electric_bikes"].sum()),
            "docks_available": int(df["docks_available"].sum()),
        },
        "by_arrondissement": by_arr,
    }


@router.get(
    "/velib/latest",
    summary="Dernier instantané Vélib' agrégé (temps réel)",
    dependencies=[Depends(require_api_key)],
)
def velib_latest() -> dict:
    path = _latest_batch_path()
    if not path:
        return {"status": "no_data", "message": "Aucun lot Vélib' — lancer le daemon micro-batch."}
    return {"status": "ok", **_aggregate(path)}


@router.websocket("/velib")
async def velib_stream(websocket: WebSocket) -> None:
    """
    Flux temps réel : pousse les agrégats Vélib' à chaque nouveau lot.

    Auth : si API_KEYS est défini, fournir ?api_key=... (les navigateurs ne
    peuvent pas poser d'en-tête sur un WebSocket → on passe par la query string).
    """
    # --- Authentification ---
    # Priorité à l'en-tête X-API-Key (non journalisé) ; repli sur la query string
    # pour les clients navigateur qui ne peuvent pas poser d'en-tête sur un WS.
    # NB : transmettre la clé en query string l'expose aux logs/proxies → préférer
    # l'en-tête quand c'est possible. Comparaison en temps constant.
    if API_KEYS:
        key = websocket.headers.get("x-api-key") or websocket.query_params.get("api_key")
        if not is_valid_api_key(key):
            await websocket.close(code=4401)  # 4401 = Unauthorized (convention applicative)
            return

    await websocket.accept()
    last_sent: str | None = None
    try:
        # Envoi immédiat du dernier état connu
        path = _latest_batch_path()
        if path:
            await websocket.send_json({"event": "snapshot", **_aggregate(path)})
            last_sent = path.name
        else:
            await websocket.send_json({"event": "waiting", "message": "En attente du daemon micro-batch…"})

        # Boucle de surveillance : push uniquement quand un nouveau lot arrive
        while True:
            await asyncio.sleep(_POLL_SECONDS)
            path = _latest_batch_path()
            if path and path.name != last_sent:
                await websocket.send_json({"event": "update", **_aggregate(path)})
                last_sent = path.name
    except WebSocketDisconnect:
        return
    except Exception:
        # Fermeture propre en cas d'erreur de lecture/sérialisation
        try:
            await websocket.close(code=1011)
        except Exception:
            pass
