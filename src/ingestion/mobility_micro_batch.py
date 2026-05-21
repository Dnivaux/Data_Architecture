"""
Mobilité Micro-batch — Bronze Ingestion
=========================================
Sources :
  - Vélib' Métropole  : GBFS temps réel (sans authentification)
  - PRIM IDFM         : SIRI StopMonitoring (clé API optionnelle via PRIM_API_KEY)

Stratégie micro-batch
---------------------
  - Collecte toutes les 60 secondes (configurable via BATCH_INTERVAL_SECONDS)
  - Sauvegarde Parquet incrémentale partitionnée par date UTC :
      data/bronze/velib/date=YYYY-MM-DD/batch_<HH-MM-SS>.parquet
      data/bronze/prim/date=YYYY-MM-DD/batch_<HH-MM-SS>.parquet
  - Arrêt propre sur SIGINT / SIGTERM (threading.Event)
  - Circuit-breaker léger : après N échecs consécutifs, pause prolongée
  - Rate-limit 429 → backoff exponentiel géré par build_session()

Utilisation
-----------
  # Lancer le daemon en arrière-plan :
  python -m src.ingestion.mobility_micro_batch

  # Ou importer pour déclencher un seul batch :
  from src.ingestion.mobility_micro_batch import run_once
  df_velib, df_prim = run_once()
"""
from __future__ import annotations

import os
import signal
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
from dotenv import load_dotenv

from .base import BRONZE_ROOT, build_session, get_logger, save_parquet

load_dotenv()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

LOG_DIR = Path(__file__).parents[2] / "logs"

BATCH_INTERVAL_SECONDS: int = int(os.getenv("BATCH_INTERVAL_SECONDS", "60"))
MAX_CONSECUTIVE_FAILURES: int = int(os.getenv("MAX_CONSECUTIVE_FAILURES", "5"))
FAILURE_BACKOFF_SECONDS: int = int(os.getenv("FAILURE_BACKOFF_SECONDS", "300"))

# Vélib' — Paris OpenData Explore API v2.1 (données temps réel, sans auth)
# Source : https://opendata.paris.fr/explore/dataset/velib-disponibilite-en-temps-reel
_VELIB_OD_BASE = "https://opendata.paris.fr/api/explore/v2.1/catalog/datasets/velib-disponibilite-en-temps-reel/records"
VELIB_API_URL = _VELIB_OD_BASE  # pagination via offset/limit

# PRIM IDFM — clé API optionnelle
PRIM_API_KEY: Optional[str] = os.getenv("PRIM_API_KEY")
PRIM_BASE_URL = "https://prim.iledefrance-mobilites.fr/marketplace"

# Arrondissements parisiens : polygones bbox simplifiés (lat_min, lat_max, lon_min, lon_max)
# Suffisant pour affecter une station à son arrondissement sans geopandas au runtime
_PARIS_ARRONDISSEMENTS: dict[str, tuple[float, float, float, float]] = {
    "Paris 1er":   (48.855, 48.865, 2.333, 2.353),
    "Paris 2e":    (48.863, 48.870, 2.341, 2.358),
    "Paris 3e":    (48.860, 48.868, 2.350, 2.365),
    "Paris 4e":    (48.848, 48.862, 2.348, 2.362),
    "Paris 5e":    (48.842, 48.857, 2.346, 2.365),
    "Paris 6e":    (48.845, 48.858, 2.330, 2.350),
    "Paris 7e":    (48.849, 48.864, 2.298, 2.334),
    "Paris 8e":    (48.868, 48.882, 2.295, 2.326),
    "Paris 9e":    (48.874, 48.885, 2.329, 2.352),
    "Paris 10e":   (48.869, 48.882, 2.349, 2.372),
    "Paris 11e":   (48.853, 48.872, 2.364, 2.392),
    "Paris 12e":   (48.833, 48.858, 2.374, 2.412),
    "Paris 13e":   (48.818, 48.845, 2.345, 2.388),
    "Paris 14e":   (48.821, 48.845, 2.310, 2.348),
    "Paris 15e":   (48.831, 48.858, 2.278, 2.319),
    "Paris 16e":   (48.845, 48.895, 2.245, 2.298),
    "Paris 17e":   (48.876, 48.899, 2.295, 2.340),
    "Paris 18e":   (48.878, 48.902, 2.328, 2.370),
    "Paris 19e":   (48.869, 48.898, 2.363, 2.408),
    "Paris 20e":   (48.852, 48.878, 2.389, 2.418),
}


def _get_arrondissement(lat: float, lon: float) -> str:
    """Affecte un arrondissement parisien par bbox. Retourne 'Hors Paris' si hors périmètre."""
    for name, (lat_min, lat_max, lon_min, lon_max) in _PARIS_ARRONDISSEMENTS.items():
        if lat_min <= lat <= lat_max and lon_min <= lon <= lon_max:
            return name
    return "Hors Paris"


# ---------------------------------------------------------------------------
# Vélib' — collecte et normalisation
# ---------------------------------------------------------------------------

VELIB_BRONZE_COLUMNS = [
    "station_code",
    "station_name",
    "arrondissement",
    "latitude",
    "longitude",
    "bikes_available",
    "mechanical_bikes",
    "electric_bikes",
    "docks_available",
    "total_capacity",
    "is_renting",
    "is_returning",
    "batch_ts",
]


def _fetch_velib(session, logger) -> pd.DataFrame:
    """
    Fusionne station_information et station_status pour produire un DataFrame
    complet avec disponibilité temps réel. Retourne un DataFrame vide si erreur.
    """
    batch_ts = datetime.now(timezone.utc)
    rows: list[dict] = []
    offset = 0
    limit  = 100  # max par page autorisé par l'API Paris OD

    while True:
        resp = session.get(
            VELIB_API_URL,
            params={"limit": limit, "offset": offset, "timezone": "UTC"},
            timeout=15,
        )
        if resp.status_code != 200:
            logger.warning("Vélib' Paris OD HTTP %d (offset=%d)", resp.status_code, offset)
            break

        payload = resp.json()
        stations = payload.get("results", [])
        if not stations:
            break

        for s in stations:
            geo  = s.get("coordonnees_geo") or {}
            lat  = float(geo.get("lat", 0.0))
            lon  = float(geo.get("lon", 0.0))

            rows.append({
                "station_code":    str(s.get("stationcode", "")),
                "station_name":    s.get("name", ""),
                "arrondissement":  _get_arrondissement(lat, lon),
                "latitude":        lat,
                "longitude":       lon,
                "bikes_available": int(s.get("numbikesavailable", 0)),
                "mechanical_bikes":int(s.get("mechanical", 0)),
                "electric_bikes":  int(s.get("ebike", 0)),
                "docks_available": int(s.get("numdocksavailable", 0)),
                "total_capacity":  int(s.get("capacity", 0)),
                "is_renting":      s.get("is_renting", "OUI") == "OUI",
                "is_returning":    s.get("is_returning", "OUI") == "OUI",
                "batch_ts":        batch_ts,
            })

        total = payload.get("total_count", 0)
        offset += limit
        if offset >= total:
            break

    df = pd.DataFrame(rows) if rows else pd.DataFrame(columns=VELIB_BRONZE_COLUMNS)
    logger.info("Vélib' Paris OD → %d stations collectées", len(df))
    return df


# ---------------------------------------------------------------------------
# PRIM IDFM — collecte et normalisation
# ---------------------------------------------------------------------------

PRIM_BRONZE_COLUMNS = [
    "stop_ref",
    "stop_name",
    "line_ref",
    "operator_ref",
    "destination_name",
    "aimed_departure_time",
    "expected_departure_time",
    "delay_seconds",
    "transport_mode",
    "vehicle_journey_ref",
    "batch_ts",
]

# Modes de transport SIRI → label lisible
_MODE_MAP = {
    "metro": "metro",
    "rail": "rer",
    "bus": "bus",
    "tram": "tram",
    "Coach": "coach",
}

# Quelques stops parisiens représentatifs pour démonstration
# En production : alimenter depuis un référentiel d'arrêts complet (NeTEx)
_DEFAULT_STOP_REFS = [
    "STIF:StopPoint:Q:471908:",   # Châtelet (Métro)
    "STIF:StopPoint:Q:22240:",    # Gare de Lyon (RER)
    "STIF:StopPoint:Q:15006:",    # Nation (Métro)
    "STIF:StopPoint:Q:18022:",    # Montparnasse (Métro)
    "STIF:StopPoint:Q:473846:",   # République (Métro)
]


def _parse_prim_response(data: dict, batch_ts: datetime) -> list[dict]:
    """
    Parse la réponse SIRI StopMonitoring de PRIM.
    Retourne une liste de dicts normalisés.
    """
    rows = []
    delivery = (
        data
        .get("Siri", {})
        .get("ServiceDelivery", {})
        .get("StopMonitoringDelivery", [{}])
    )
    if isinstance(delivery, dict):
        delivery = [delivery]

    for stop_delivery in delivery:
        visits = stop_delivery.get("MonitoredStopVisit", [])
        for visit in visits:
            mvj = visit.get("MonitoredVehicleJourney", {})
            call = mvj.get("MonitoredCall", {})

            aimed_str = call.get("AimedDepartureTime", "")
            expected_str = call.get("ExpectedDepartureTime", "") or aimed_str

            try:
                aimed_dt = datetime.fromisoformat(aimed_str.replace("Z", "+00:00")) if aimed_str else None
                expected_dt = datetime.fromisoformat(expected_str.replace("Z", "+00:00")) if expected_str else None
                delay_s = int((expected_dt - aimed_dt).total_seconds()) if (aimed_dt and expected_dt) else 0
            except (ValueError, TypeError):
                aimed_dt, expected_dt, delay_s = None, None, 0

            raw_mode = mvj.get("VehicleMode", "bus")
            mode = _MODE_MAP.get(str(raw_mode).lower(), str(raw_mode).lower())

            rows.append(
                {
                    "stop_ref": visit.get("MonitoringRef", {}).get("value", ""),
                    "stop_name": call.get("StopPointName", {}).get("value", ""),
                    "line_ref": mvj.get("LineRef", {}).get("value", ""),
                    "operator_ref": mvj.get("OperatorRef", {}).get("value", ""),
                    "destination_name": mvj.get("DestinationName", [{}])[0].get("value", "")
                    if isinstance(mvj.get("DestinationName"), list)
                    else mvj.get("DestinationName", {}).get("value", ""),
                    "aimed_departure_time": aimed_dt,
                    "expected_departure_time": expected_dt,
                    "delay_seconds": delay_s,
                    "transport_mode": mode,
                    "vehicle_journey_ref": mvj.get("FramedVehicleJourneyRef", {}).get(
                        "DatedVehicleJourneyRef", ""
                    ),
                    "batch_ts": batch_ts,
                }
            )
    return rows


def _fetch_prim(session, logger, stop_refs: list[str] | None = None) -> pd.DataFrame:
    """
    Interroge l'API PRIM SIRI StopMonitoring pour chaque stop_ref.
    Retourne un DataFrame vide si PRIM_API_KEY absent ou erreur.
    """
    if not PRIM_API_KEY:
        logger.info("PRIM_API_KEY absent — ingestion PRIM ignorée pour ce batch")
        return pd.DataFrame(columns=PRIM_BRONZE_COLUMNS)

    batch_ts = datetime.now(timezone.utc)
    refs = stop_refs or _DEFAULT_STOP_REFS
    all_rows: list[dict] = []

    headers = {
        "apikey": PRIM_API_KEY,
        "Accept": "application/json",
    }

    for stop_ref in refs:
        params = {
            "MonitoringRef": stop_ref,
            "MaximumStopVisits": 5,
        }
        try:
            resp = session.get(
                f"{PRIM_BASE_URL}/stop-monitoring",
                headers=headers,
                params=params,
            )
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 10))
                logger.warning("PRIM rate-limit sur %s — attente %ds", stop_ref, retry_after)
                time.sleep(retry_after)
                continue
            if resp.status_code != 200:
                logger.warning("PRIM HTTP %d pour stop %s: %s", resp.status_code, stop_ref, resp.text[:200])
                continue

            rows = _parse_prim_response(resp.json(), batch_ts)
            all_rows.extend(rows)
            logger.debug("PRIM stop %s → %d passages", stop_ref, len(rows))

        except Exception as exc:
            logger.error("PRIM erreur stop %s : %s", stop_ref, exc)

        # Pause courtoise entre requêtes (évite le rate-limit)
        time.sleep(0.2)

    df = (
        pd.DataFrame(all_rows, columns=PRIM_BRONZE_COLUMNS)
        if all_rows
        else pd.DataFrame(columns=PRIM_BRONZE_COLUMNS)
    )
    logger.info("PRIM → %d passages collectés", len(df))
    return df


# ---------------------------------------------------------------------------
# Sauvegarde incrémentale Bronze
# ---------------------------------------------------------------------------

def _save_batch(df: pd.DataFrame, source: str, batch_ts: datetime) -> Path | None:
    """Persiste un batch en Parquet avec partitionnement par date UTC."""
    if df.empty:
        return None

    date_str = batch_ts.strftime("%Y-%m-%d")
    filename = f"batch_{batch_ts.strftime('%H-%M-%S')}.parquet"
    path = save_parquet(
        df,
        source=source,
        partition_col="date",
        partition_value=date_str,
        filename=filename,
    )
    return path


# ---------------------------------------------------------------------------
# Interface publique : run_once() et run_daemon()
# ---------------------------------------------------------------------------

def run_once(
    stop_refs: list[str] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Exécute un seul batch de collecte (Vélib' + PRIM).

    Paramètres
    ----------
    stop_refs : liste optionnelle de MonitoringRef PRIM (format STIF:StopPoint:Q:...)
        Si None, utilise _DEFAULT_STOP_REFS.

    Retourne
    --------
    (df_velib, df_prim) — DataFrames bruts du batch courant.
    """
    logger = get_logger("mobility_micro_batch", LOG_DIR)
    session = build_session(retries=3, backoff_factor=2.0, timeout=15)

    batch_ts = datetime.now(timezone.utc)
    logger.info("Batch mobilité démarré à %s", batch_ts.isoformat())

    df_velib = _fetch_velib(session, logger)
    df_prim = _fetch_prim(session, logger, stop_refs=stop_refs)

    path_v = _save_batch(df_velib, "velib", batch_ts)
    path_p = _save_batch(df_prim, "prim", batch_ts)

    if path_v:
        logger.info("Vélib' sauvegardé → %s", path_v)
    if path_p:
        logger.info("PRIM sauvegardé → %s", path_p)

    return df_velib, df_prim


def run_daemon(
    interval_seconds: int = BATCH_INTERVAL_SECONDS,
    stop_refs: list[str] | None = None,
    stop_event: threading.Event | None = None,
) -> None:
    """
    Boucle micro-batch : collecte toutes les `interval_seconds` secondes.

    Arrêt propre :
      - Via stop_event.set() depuis un thread parent
      - Via SIGINT (Ctrl+C) ou SIGTERM

    Circuit-breaker : après MAX_CONSECUTIVE_FAILURES échecs consécutifs,
    pause de FAILURE_BACKOFF_SECONDS avant de reprendre.

    Paramètres
    ----------
    interval_seconds : int
        Intervalle entre deux batches (défaut : 60s, configurable via env).
    stop_refs : list[str], optional
        Références d'arrêts PRIM à interroger.
    stop_event : threading.Event, optional
        Event externe pour arrêter le daemon proprement.
    """
    logger = get_logger("mobility_micro_batch", LOG_DIR)

    _stop = stop_event or threading.Event()
    consecutive_failures = 0

    def _handle_signal(signum, frame):
        logger.info("Signal %d reçu — arrêt du daemon micro-batch", signum)
        _stop.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    logger.info(
        "Daemon mobilité micro-batch démarré (intervalle=%ds, PRIM=%s)",
        interval_seconds,
        "activé" if PRIM_API_KEY else "désactivé (clé absente)",
    )

    while not _stop.is_set():
        batch_start = time.monotonic()

        try:
            run_once(stop_refs=stop_refs)
            consecutive_failures = 0

        except Exception as exc:
            consecutive_failures += 1
            logger.error(
                "Erreur batch #%d : %s", consecutive_failures, exc, exc_info=True
            )

            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                logger.critical(
                    "%d échecs consécutifs — pause circuit-breaker %ds",
                    consecutive_failures,
                    FAILURE_BACKOFF_SECONDS,
                )
                _stop.wait(timeout=FAILURE_BACKOFF_SECONDS)
                consecutive_failures = 0
                continue

        elapsed = time.monotonic() - batch_start
        sleep_for = max(0.0, interval_seconds - elapsed)

        logger.debug("Prochain batch dans %.1fs", sleep_for)
        _stop.wait(timeout=sleep_for)

    logger.info("Daemon mobilité micro-batch arrêté proprement.")


# ---------------------------------------------------------------------------
# Point d'entrée CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Micro-batch mobilité Bronze : Vélib' + PRIM IDFM",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples :
  # Lancer le daemon (Ctrl+C pour arrêter)
  python -m src.ingestion.mobility_micro_batch

  # Un seul batch puis quitter
  python -m src.ingestion.mobility_micro_batch --once

  # Intervalle personnalisé (30s) avec stops PRIM spécifiques
  python -m src.ingestion.mobility_micro_batch --interval 30 \\
      --stop-refs STIF:StopPoint:Q:471908: STIF:StopPoint:Q:22240:
""",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Exécuter un seul batch puis quitter",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=BATCH_INTERVAL_SECONDS,
        metavar="SECONDES",
        help=f"Intervalle entre batches (défaut: {BATCH_INTERVAL_SECONDS}s)",
    )
    parser.add_argument(
        "--stop-refs",
        nargs="*",
        metavar="REF",
        help="Références PRIM MonitoringRef à interroger",
    )
    args = parser.parse_args()

    if args.once:
        run_once(stop_refs=args.stop_refs)
    else:
        run_daemon(interval_seconds=args.interval, stop_refs=args.stop_refs)
