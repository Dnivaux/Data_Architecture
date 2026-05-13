"""
Full Data Pipeline Orchestrator
==================================
Runs: Bronze → Silver → Gold layers in sequence
Bronze: Raw data ingestion from APIs
Silver: Spatial processing & scoring
Gold:   API-ready tables
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.ingestion.base import get_logger
from src.gold.build import build_gold_layer
from src.silver.aggregation import build_silver_layer

LOG_DIR = Path(__file__).parent / "logs"
logger = get_logger("pipeline_full", LOG_DIR)


def run_full_pipeline(
    skip_bronze: bool = False,
    skip_silver: bool = False,
    skip_gold: bool = False,
) -> None:
    """Run the complete data pipeline."""
    started = datetime.now(timezone.utc)
    logger.info("=" * 70)
    logger.info("Full Data Pipeline (Bronze → Silver → Gold)")
    logger.info("Started: %s", started.isoformat())
    logger.info("=" * 70)

    if not skip_bronze:
        logger.info("\n>>> BRONZE LAYER – Ingesting raw data from APIs")
        try:
            from main import run_pipeline
            from argparse import Namespace

            # Import and run the main ingestion pipeline
            args = Namespace(
                sources=["dvf", "osm", "boundaries", "revenus", "air_quality", "crime"],
                date_min=None,
                date_max=None,
                dry_run=False,
            )
            # Note: This will call run_pipeline from main.py
            # For now, log that Bronze should be run separately
            logger.info("Bronze layer: Run 'python main.py' to ingest fresh data")
        except Exception as e:
            logger.error("Bronze layer failed: %s", e)
            return

    if not skip_silver:
        logger.info("\n>>> SILVER LAYER – Processing & scoring")
        try:
            t0 = time.perf_counter()
            build_silver_layer()
            elapsed = time.perf_counter() - t0
            logger.info("Silver layer complete (%.1fs)", elapsed)
        except Exception as e:
            logger.error("Silver layer failed: %s", e, exc_info=True)
            if not skip_gold:
                logger.warning("Skipping Gold layer due to Silver failure")
                return

    if not skip_gold:
        logger.info("\n>>> GOLD LAYER – Building API-ready tables")
        try:
            t0 = time.perf_counter()
            build_gold_layer()
            elapsed = time.perf_counter() - t0
            logger.info("Gold layer complete (%.1fs)", elapsed)
        except Exception as e:
            logger.error("Gold layer failed: %s", e, exc_info=True)
            return

    finished = datetime.now(timezone.utc)
    total = (finished - started).total_seconds()
    logger.info("\n" + "=" * 70)
    logger.info("Pipeline finished: %s", finished.isoformat())
    logger.info("Total time: %.1fs", total)
    logger.info("=" * 70)
    logger.info("\nNext step: Start the API with 'python -m api.main'")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Orchestrate full data pipeline (Bronze → Silver → Gold)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--skip-bronze",
        action="store_true",
        help="Skip Bronze layer (assume data already ingested)",
    )
    parser.add_argument(
        "--skip-silver",
        action="store_true",
        help="Skip Silver layer",
    )
    parser.add_argument(
        "--skip-gold",
        action="store_true",
        help="Skip Gold layer",
    )
    args = parser.parse_args()

    run_full_pipeline(
        skip_bronze=args.skip_bronze,
        skip_silver=args.skip_silver,
        skip_gold=args.skip_gold,
    )
