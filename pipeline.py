"""


Flags principaux
----------------
  --mobility-once    : 1 batch Vélib'/PRIM intégré au pipeline Bronze
  --mobility-daemon  : daemon micro-batch en thread arrière-plan
  --skip-pg          : ignore l'export PostgreSQL
  --pg-dry-run       : teste la connexion PG sans écrire
  --skip-bronze/silver/gold : saute une couche
"""
from __future__ import annotations

import argparse
import importlib
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.ingestion.base import get_logger
from src.gold.build import build_gold_layer
from src.silver.aggregation import build_silver_layer

LOG_DIR = Path(__file__).parent / "logs"
logger = get_logger("pipeline_full", LOG_DIR)


# ---------------------------------------------------------------------------
# Bronze — indicateurs statiques
# ---------------------------------------------------------------------------

def _run_static_indicators() -> None:
    """Ingestion séquentielle des 3 modules Bronze statiques (nouveaux indicateurs)."""
    indicators = [
        ("Connectivité & Télétravail", "src.ingestion.connectivity",       "ingest"),
        ("Santé Environnementale",     "src.ingestion.health_environment", "ingest"),
        ("Tranquillité vs Dynamisme",  "src.ingestion.tranquility",        "ingest"),
    ]
    logger.info(">>> BRONZE — indicateurs stratégiques (3 sources statiques)")
    for label, module_path, func_name in indicators:
        logger.info("  [%s]", label)
        try:
            mod = importlib.import_module(module_path)
            t0 = time.perf_counter()
            df = getattr(mod, func_name)()
            logger.info("    OK — %d lignes (%.1fs)", len(df), time.perf_counter() - t0)
        except Exception as exc:
            logger.error("    ERREUR [%s] : %s", label, exc, exc_info=True)


# ---------------------------------------------------------------------------
# Bronze — daemon mobilité micro-batch
# ---------------------------------------------------------------------------

def _start_mobility_daemon(stop_event: threading.Event) -> threading.Thread:
    from src.ingestion.mobility_micro_batch import run_daemon
    t = threading.Thread(
        target=run_daemon,
        kwargs={"stop_event": stop_event},
        name="mobility-micro-batch",
        daemon=True,
    )
    t.start()
    logger.info("Daemon mobilité démarré (thread : %s)", t.name)
    return t


# ---------------------------------------------------------------------------
# Pipeline principal
# ---------------------------------------------------------------------------

def run_full_pipeline(
    skip_bronze: bool = False,
    skip_silver: bool = False,
    skip_gold: bool = False,
    skip_pg: bool = False,
    mobility_once: bool = False,
    mobility_daemon: bool = False,
    pg_dry_run: bool = False,
    db_url: str | None = None,
) -> None:
    """
    Orchestre le pipeline Medallion complet :
    Bronze statique → Bronze mobilité (opt.) → Silver → Gold → PostgreSQL.

    Paramètres
    ----------
    skip_bronze     : saute l'ingestion Bronze (données déjà présentes)
    skip_silver     : saute la couche Silver
    skip_gold       : saute la couche Gold
    skip_pg         : saute l'export PostgreSQL
    mobility_once   : exécute 1 seul batch Vélib'/PRIM après les sources statiques
    mobility_daemon : lance le daemon micro-batch en thread arrière-plan
    pg_dry_run      : vérifie la connexion PG sans écrire
    db_url          : URL SQLAlchemy (surcharge DATABASE_URL)
    """
    started = datetime.now(timezone.utc)
    logger.info("=" * 70)
    logger.info("Pipeline Lakehouse Medallion (Bronze → Silver → Gold → PG)")
    logger.info("Démarré : %s", started.isoformat())
    logger.info("=" * 70)

    mobility_stop_event: threading.Event | None = None

    # --- Daemon mobilité (non-bloquant, lancé en premier) ---
    if mobility_daemon:
        mobility_stop_event = threading.Event()
        _start_mobility_daemon(mobility_stop_event)

    # ================================================================
    # COUCHE BRONZE
    # ================================================================
    if not skip_bronze:
        logger.info("\n%s\n>>> BRONZE LAYER\n%s", "─" * 50, "─" * 50)

        # Sources historiques (DVF, OSM, boundaries, revenus, air_quality, crime)
        try:
            logger.info("[1/4] Sources historiques (DVF, OSM, boundaries, crime…)")
            logger.info("      → Exécuter 'python main.py' pour ingérer ces sources")
        except Exception as exc:
            logger.error("Sources historiques échouées : %s", exc)

        # Nouveaux indicateurs statiques
        logger.info("[2/4] Indicateurs stratégiques statiques")
        _run_static_indicators()

        # Batch mobilité unique (optionnel)
        if mobility_once:
            logger.info("[3/4] Batch mobilité unique (Vélib' + PRIM)")
            try:
                from src.ingestion.mobility_micro_batch import run_once
                t0 = time.perf_counter()
                df_v, df_p = run_once()
                logger.info(
                    "    Vélib' : %d stations, PRIM : %d passages (%.1fs)",
                    len(df_v), len(df_p), time.perf_counter() - t0,
                )
            except Exception as exc:
                logger.error("    Mobilité batch échoué : %s", exc, exc_info=True)
        else:
            logger.info("[3/4] Batch mobilité ignoré (pas de --mobility-once)")

        logger.info("[4/4] Bronze complet")

    # ================================================================
    # COUCHE SILVER
    # ================================================================
    if not skip_silver:
        logger.info("\n%s\n>>> SILVER LAYER\n%s", "─" * 50, "─" * 50)
        try:
            t0 = time.perf_counter()
            build_silver_layer()
            logger.info("Silver terminé (%.1fs)", time.perf_counter() - t0)
        except Exception as exc:
            logger.error("Silver échoué : %s", exc, exc_info=True)
            if not skip_gold:
                logger.warning("Gold ignoré suite à l'échec Silver")
                _finalize(started, mobility_stop_event, mobility_daemon)
                return

    # ================================================================
    # COUCHE GOLD
    # ================================================================
    if not skip_gold:
        logger.info("\n%s\n>>> GOLD LAYER\n%s", "─" * 50, "─" * 50)
        try:
            t0 = time.perf_counter()
            build_gold_layer()
            logger.info("Gold terminé (%.1fs)", time.perf_counter() - t0)
        except Exception as exc:
            logger.error("Gold échoué : %s", exc, exc_info=True)
            if not skip_pg:
                logger.warning("Export PG ignoré suite à l'échec Gold")
                _finalize(started, mobility_stop_event, mobility_daemon)
                return

    # ================================================================
    # EXPORT POSTGRESQL
    # ================================================================
    if not skip_pg:
        logger.info("\n%s\n>>> POSTGRESQL EXPORT\n%s", "─" * 50, "─" * 50)
        try:
            from src.gold.export_pg import export_all
            t0 = time.perf_counter()
            results = export_all(db_url=db_url, dry_run=pg_dry_run)
            elapsed = time.perf_counter() - t0
            total_rows = sum(v for v in results.values() if v >= 0)
            logger.info(
                "Export PG terminé : %d tables, %d lignes totales (%.1fs)",
                len(results), total_rows, elapsed,
            )
            for table, count in results.items():
                status = "✓" if count >= 0 else "✗"
                logger.info("  %s  %s : %d lignes", status, table, count)
        except Exception as exc:
            logger.error("Export PostgreSQL échoué : %s", exc, exc_info=True)
            logger.error(
                "Vérifier DATABASE_URL dans .env (défaut : "
                "postgresql://postgres:postgres@localhost:5432/urbandata)"
            )

    _finalize(started, mobility_stop_event, mobility_daemon)


def _finalize(
    started: datetime,
    mobility_stop_event: threading.Event | None,
    mobility_daemon: bool,
) -> None:
    finished = datetime.now(timezone.utc)
    total = (finished - started).total_seconds()
    logger.info("\n" + "=" * 70)
    logger.info("Pipeline terminé : %s", finished.isoformat())
    logger.info("Durée totale    : %.1fs", total)
    logger.info("=" * 70)
    logger.info("Prochaine étape : python -m api.main")

    if mobility_stop_event and mobility_daemon:
        logger.info(
            "Daemon mobilité actif en arrière-plan — "
            "Ctrl+C ou stop_event.set() pour l'arrêter."
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Pipeline Lakehouse Medallion (Bronze → Silver → Gold → PostgreSQL)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples :
  # Pipeline complet avec export PostgreSQL
  python pipeline.py

  # Pipeline complet + 1 batch mobilité
  python pipeline.py --mobility-once

  # Pipeline complet + daemon mobilité en arrière-plan
  python pipeline.py --mobility-daemon

  # Silver + Gold + PG uniquement (Bronze déjà ingéré)
  python pipeline.py --skip-bronze

  # Tester la connexion PG sans écrire
  python pipeline.py --skip-bronze --skip-silver --pg-dry-run

  # Sans export PostgreSQL (si PG non disponible)
  python pipeline.py --skip-pg

  # URL de connexion personnalisée
  python pipeline.py --db-url postgresql://user:pass@host:5432/mydb
""",
    )
    parser.add_argument("--skip-bronze",    action="store_true", help="Sauter la couche Bronze")
    parser.add_argument("--skip-silver",    action="store_true", help="Sauter la couche Silver")
    parser.add_argument("--skip-gold",      action="store_true", help="Sauter la couche Gold")
    parser.add_argument("--skip-pg",        action="store_true", help="Sauter l'export PostgreSQL")
    parser.add_argument("--mobility-once",  action="store_true", help="1 batch Vélib'/PRIM en Bronze")
    parser.add_argument("--mobility-daemon",action="store_true", help="Daemon micro-batch en arrière-plan")
    parser.add_argument("--pg-dry-run",     action="store_true", help="Tester PG sans écrire")
    parser.add_argument("--db-url",         metavar="URL",       help="URL SQLAlchemy PostgreSQL")
    args = parser.parse_args()

    run_full_pipeline(
        skip_bronze=args.skip_bronze,
        skip_silver=args.skip_silver,
        skip_gold=args.skip_gold,
        skip_pg=args.skip_pg,
        mobility_once=args.mobility_once,
        mobility_daemon=args.mobility_daemon,
        pg_dry_run=args.pg_dry_run,
        db_url=args.db_url,
    )
