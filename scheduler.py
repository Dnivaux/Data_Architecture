"""
Planificateur d'ingestion — Urban Data Explorer  [DÉPRÉCIÉ]
================================================
⚠️  DÉPRÉCIÉ — remplacé par Apache Airflow (voir dags/ et docker-compose.yml).
    Lancer plutôt :  docker compose up -d airflow-webserver airflow-scheduler
    puis ouvrir l'UI http://localhost:8080 (airflow / airflow).
    Les DAGs urban_data_pipeline / air_quality_refresh / mobility_micro_batch
    reproduisent à l'identique les cadences ci-dessous via PythonOperator.

    Ce module `schedule` est conservé pour un repli local sans Docker.

Ingestion *planifiée* (attendu consigne : « pipeline d'ingestion planifiée
permettant de récupérer régulièrement les différentes sources »).

Utilise la librairie `schedule` (déjà dans requirements.txt). Trois cadences :

  • Qualité de l'air + pollen (Open-Meteo) : toutes les heures
      → données très fraîches, source gratuite sans quota strict
  • Mobilité micro-batch (Vélib')          : géré par son propre daemon
      (python pipeline.py --mobility-daemon) — non dupliqué ici
  • Pipeline complet (Bronze→Silver→Gold→PG) : tous les jours à 03:00
      → sources lourdes (DVF, INSEE, SSMSI, OSM, Paris OD)

Usage
-----
  # Démarrer le planificateur (bloquant, Ctrl+C pour arrêter)
  python scheduler.py

  # Cadences personnalisées
  python scheduler.py --air-minutes 30 --full-at 02:30

  # Lancer immédiatement un cycle complet au démarrage puis planifier
  python scheduler.py --run-now

Déploiement production : exécuter ce script sous un superviseur (systemd,
Docker `restart: unless-stopped`, ou Task Scheduler Windows). Pour un vrai
ordonnanceur distribué, brancher ces appels dans Airflow / Prefect.
"""
from __future__ import annotations

import argparse
import time
from datetime import datetime, timezone
from pathlib import Path

import schedule

from src.ingestion.base import get_logger

LOG_DIR = Path(__file__).parent / "logs"
logger = get_logger("scheduler", LOG_DIR)


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

def job_air_quality() -> None:
    """Rafraîchit la qualité de l'air + pollen (Open-Meteo) puis recalcule Silver/Gold."""
    logger.info("[JOB] Qualité de l'air + pollen (Open-Meteo)")
    try:
        from src.ingestion.open_meteo_air import ingest as ingest_air
        df = ingest_air()
        logger.info("  air_quality OK — %d arrondissements", len(df))
        # Recalcul léger Silver+Gold pour propager les nouvelles valeurs
        from src.silver.aggregation import build_silver_layer
        from src.gold.build import build_gold_layer
        build_silver_layer()
        build_gold_layer()
        logger.info("  Silver+Gold rafraîchis")
    except Exception as exc:
        logger.error("  Échec job air_quality : %s", exc, exc_info=True)


def job_full_pipeline() -> None:
    """Exécute le pipeline complet (toutes sources Bronze → Silver → Gold → PG)."""
    logger.info("[JOB] Pipeline complet quotidien")
    try:
        import main as bronze_main           # ingestion des sources historiques
        bronze_main.run_pipeline(_default_bronze_args())
    except SystemExit:
        pass  # main.run_pipeline appelle sys.exit(1) si une source échoue
    except Exception as exc:
        logger.error("  Échec ingestion Bronze : %s", exc, exc_info=True)

    try:
        from pipeline import run_full_pipeline
        run_full_pipeline(skip_bronze=True, mobility_once=True)
        logger.info("  Pipeline complet terminé")
    except Exception as exc:
        logger.error("  Échec pipeline Silver/Gold/PG : %s", exc, exc_info=True)


def _default_bronze_args() -> argparse.Namespace:
    """Args par défaut pour main.run_pipeline (toutes sources, pas de dry-run)."""
    return argparse.Namespace(
        sources=["dvf", "osm", "boundaries", "revenus", "air_quality", "crime"],
        date_min=None, date_max=None, dry_run=False,
    )


# ---------------------------------------------------------------------------
# Boucle principale
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Planificateur d'ingestion Urban Data Explorer")
    parser.add_argument("--air-minutes", type=int, default=60,
                        help="Cadence de rafraîchissement air/pollen en minutes (défaut: 60)")
    parser.add_argument("--full-at", default="03:00",
                        help="Heure du pipeline complet quotidien HH:MM (défaut: 03:00)")
    parser.add_argument("--run-now", action="store_true",
                        help="Lance un cycle air + pipeline complet immédiatement au démarrage")
    args = parser.parse_args()

    schedule.every(args.air_minutes).minutes.do(job_air_quality)
    schedule.every().day.at(args.full_at).do(job_full_pipeline)

    logger.info("=" * 60)
    logger.info("Planificateur démarré : %s", datetime.now(timezone.utc).isoformat())
    logger.info("  • air/pollen toutes les %d min", args.air_minutes)
    logger.info("  • pipeline complet chaque jour à %s", args.full_at)
    logger.info("=" * 60)

    if args.run_now:
        logger.info("--run-now : exécution immédiate")
        job_air_quality()
        job_full_pipeline()

    try:
        while True:
            schedule.run_pending()
            time.sleep(30)
    except KeyboardInterrupt:
        logger.info("Planificateur arrêté (Ctrl+C)")


if __name__ == "__main__":
    main()
