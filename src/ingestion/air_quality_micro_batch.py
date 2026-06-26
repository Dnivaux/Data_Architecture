"""
Qualité de l'air Micro-batch — Bronze Ingestion (temps réel)
=============================================================
Source : Open-Meteo Air Quality API (CAMS Europe) — gratuit, sans clé.

Pendant que `open_meteo_air.ingest()` produit un snapshot quotidien consommé par
le pipeline Silver/Gold (data/bronze/air_quality/date=.../part-0.parquet), ce
module fait du **streaming** : il interroge l'API en continu et écrit des lots
horodatés dans une source SÉPARÉE, sans perturber le pipeline batch :

    data/bronze/air_quality_live/date=YYYY-MM-DD/batch_<HH-MM-SS>.parquet

Le routeur /api/live/air/latest observe le dernier lot et le sert agrégé,
exactement comme /api/live/velib/latest pour la mobilité (critère RNCP C2.2 :
traitement « au fil de l'eau »).

NB : l'European AQI Open-Meteo (`current`) se rafraîchit ~toutes les heures côté
source. Un intervalle de collecte plus court re-capture la même valeur ; on
garde donc un défaut de 15 min (configurable), suffisant pour une démo live
tout en restant courtois avec l'API publique.

Utilisation
-----------
  # Daemon (Ctrl+C pour arrêter)
  python -m src.ingestion.air_quality_micro_batch

  # Un seul lot puis quitter
  python -m src.ingestion.air_quality_micro_batch --once

  # Intervalle personnalisé (60s, pour démo)
  python -m src.ingestion.air_quality_micro_batch --interval 60
"""
from __future__ import annotations

import os
import signal
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from .base import build_session, get_logger, save_parquet
# Réutilisation de la logique d'ingestion air existante (DRY)
from .open_meteo_air import (
    BRONZE_COLUMNS,
    _fetch_one,
    _load_centroids,
)

load_dotenv()

LOG_DIR = Path(__file__).parents[2] / "logs"
LIVE_SOURCE = "air_quality_live"

# La source Open-Meteo (current AQI) se rafraîchit ~horairement → 15 min par défaut.
BATCH_INTERVAL_SECONDS: int = int(os.getenv("AIR_BATCH_INTERVAL_SECONDS", "900"))
MAX_CONSECUTIVE_FAILURES: int = int(os.getenv("AIR_MAX_CONSECUTIVE_FAILURES", "5"))
FAILURE_BACKOFF_SECONDS: int = int(os.getenv("AIR_FAILURE_BACKOFF_SECONDS", "600"))


def run_once() -> pd.DataFrame:
    """Collecte un lot temps réel (20 arrondissements) et le persiste, horodaté."""
    logger = get_logger("air_quality_micro_batch", LOG_DIR)
    batch_ts = datetime.now(timezone.utc)
    centroids = _load_centroids(logger)
    session = build_session(retries=3, backoff_factor=0.5, timeout=20)

    logger.info("Lot air démarré à %s (%d arrondissements)", batch_ts.isoformat(), len(centroids))

    records: list[dict] = []
    for arr in sorted(centroids):
        lat, lon = centroids[arr]
        try:
            row = _fetch_one(session, logger, arr, lat, lon, batch_ts)
            if row:
                records.append(row)
        except Exception as exc:  # noqa: BLE001
            logger.error("Arr %02d : échec récupération — %s", arr, exc)

    if not records:
        logger.error("Aucune donnée air récupérée — lot non créé")
        return pd.DataFrame(columns=BRONZE_COLUMNS)

    df = pd.DataFrame(records)[BRONZE_COLUMNS]
    df["aqi_level"] = pd.to_numeric(df["aqi_level"], errors="coerce").astype("Int64")
    df["indice_atmo_num"] = df["aqi_level"]

    date_str = batch_ts.strftime("%Y-%m-%d")
    filename = f"batch_{batch_ts.strftime('%H-%M-%S')}.parquet"
    path = save_parquet(
        df, source=LIVE_SOURCE,
        partition_col="date", partition_value=date_str, filename=filename,
    )
    logger.info(
        "Lot air sauvegardé → %s (AQI moyen=%.0f)",
        path, df["european_aqi"].mean(),
    )
    return df


def run_daemon(
    interval_seconds: int = BATCH_INTERVAL_SECONDS,
    stop_event: threading.Event | None = None,
) -> None:
    """Boucle micro-batch : collecte la qualité de l'air toutes les `interval_seconds`.

    Arrêt propre via stop_event.set(), SIGINT (Ctrl+C) ou SIGTERM.
    Circuit-breaker : après MAX_CONSECUTIVE_FAILURES échecs, pause prolongée.
    """
    logger = get_logger("air_quality_micro_batch", LOG_DIR)
    _stop = stop_event or threading.Event()
    consecutive_failures = 0

    def _handle_signal(signum, frame):  # noqa: ANN001
        logger.info("Signal %d reçu — arrêt du daemon air", signum)
        _stop.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    logger.info("Daemon qualité de l'air démarré (intervalle=%ds)", interval_seconds)

    while not _stop.is_set():
        batch_start = time.monotonic()
        try:
            run_once()
            consecutive_failures = 0
        except Exception as exc:  # noqa: BLE001
            consecutive_failures += 1
            logger.error("Erreur lot air #%d : %s", consecutive_failures, exc, exc_info=True)
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                logger.critical(
                    "%d échecs consécutifs — pause circuit-breaker %ds",
                    consecutive_failures, FAILURE_BACKOFF_SECONDS,
                )
                _stop.wait(timeout=FAILURE_BACKOFF_SECONDS)
                consecutive_failures = 0
                continue

        elapsed = time.monotonic() - batch_start
        _stop.wait(timeout=max(0.0, interval_seconds - elapsed))

    logger.info("Daemon qualité de l'air arrêté proprement.")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Micro-batch qualité de l'air Bronze (Open-Meteo, temps réel)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--once", action="store_true", help="Exécuter un seul lot puis quitter")
    parser.add_argument("--interval", type=int, default=BATCH_INTERVAL_SECONDS,
                        metavar="SECONDES", help=f"Intervalle entre lots (défaut: {BATCH_INTERVAL_SECONDS}s)")
    args = parser.parse_args()

    if args.once:
        run_once()
    else:
        run_daemon(interval_seconds=args.interval)
