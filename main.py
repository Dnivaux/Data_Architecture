"""
Urban Data Explorer — Bronze Layer Pipeline
============================================
Runs all ingestion modules in sequence and reports a status summary.

Usage
-----
# Full pipeline
python main.py

# Specific sources only
python main.py --sources dvf osm boundaries

# DVF with custom date range
python main.py --sources dvf --date-min 2023-01-01 --date-max 2023-12-31

# Dry-run (log what would run, skip actual HTTP calls)
python main.py --dry-run
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

# Ensure src/ is on the path when running from project root
sys.path.insert(0, str(Path(__file__).parent))

from src.ingestion.base import get_logger

LOG_DIR = Path(__file__).parent / "logs"
logger = get_logger("pipeline", LOG_DIR)

# ---------------------------------------------------------------------------
# Source registry
# ---------------------------------------------------------------------------

ALL_SOURCES = ["dvf", "osm", "boundaries", "revenus", "air_quality", "crime"]


def _build_task_map(args: argparse.Namespace) -> dict[str, Callable]:
    """Import modules lazily to avoid loading all dependencies upfront."""
    from src.ingestion import (
        ingest_air_quality,
        ingest_boundaries,
        ingest_crime,
        ingest_dvf,
        ingest_osm,
        ingest_revenus,
    )

    def dvf():
        return ingest_dvf(date_min=args.date_min, date_max=args.date_max)

    def osm():
        return ingest_osm()

    def boundaries():
        return ingest_boundaries()

    def revenus():
        return ingest_revenus()

    def air_quality():
        return ingest_air_quality()

    def crime():
        return ingest_crime()

    return {
        "dvf": dvf,
        "osm": osm,
        "boundaries": boundaries,
        "revenus": revenus,
        "air_quality": air_quality,
        "crime": crime,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Urban Data Explorer – Bronze ingestion pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--sources",
        nargs="+",
        choices=ALL_SOURCES,
        default=ALL_SOURCES,
        metavar="SOURCE",
        help=f"Sources to ingest (default: all). Choices: {ALL_SOURCES}",
    )
    parser.add_argument(
        "--date-min",
        default=None,
        help="DVF date range start (YYYY-MM-DD). Defaults to Jan 1 of current year.",
    )
    parser.add_argument(
        "--date-max",
        default=None,
        help="DVF date range end (YYYY-MM-DD). Defaults to today.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log which sources would run without executing them.",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_pipeline(args: argparse.Namespace) -> None:
    started_at = datetime.now(timezone.utc)
    logger.info("=" * 60)
    logger.info("Bronze pipeline started at %s", started_at.isoformat())
    logger.info("Sources: %s", args.sources)
    logger.info("=" * 60)

    if args.dry_run:
        logger.info("[DRY RUN] Would execute: %s", args.sources)
        return

    task_map = _build_task_map(args)
    results: dict[str, str] = {}

    for source in args.sources:
        t0 = time.perf_counter()
        logger.info("\n>>> Running: %s", source.upper())
        try:
            df = task_map[source]()
            elapsed = time.perf_counter() - t0
            rows = len(df) if df is not None else 0
            results[source] = f"OK — {rows:,} rows ({elapsed:.1f}s)"
            logger.info("<<< %s done: %d rows in %.1fs", source.upper(), rows, elapsed)
        except Exception as exc:  # noqa: BLE001
            elapsed = time.perf_counter() - t0
            results[source] = f"FAILED — {type(exc).__name__}: {exc}"
            logger.exception("<<< %s FAILED after %.1fs", source.upper(), elapsed)

    # Summary
    finished_at = datetime.now(timezone.utc)
    total = (finished_at - started_at).total_seconds()
    logger.info("\n%s", "=" * 60)
    logger.info("Pipeline finished at %s (%.1fs total)", finished_at.isoformat(), total)
    logger.info("%-15s %s", "SOURCE", "RESULT")
    logger.info("-" * 60)
    for source, result in results.items():
        status_icon = "✓" if result.startswith("OK") else "✗"
        logger.info("%s %-13s %s", status_icon, source, result)
    logger.info("=" * 60)

    if any(r.startswith("FAILED") for r in results.values()):
        sys.exit(1)


if __name__ == "__main__":
    args = parse_args()
    run_pipeline(args)
