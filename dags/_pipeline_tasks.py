"""
Fonctions-tâches partagées par les DAGs Airflow
================================================
Ces callables enveloppent les fonctions existantes du pipeline
(src.ingestion, src.silver, src.gold) pour qu'Airflow les exécute en
PythonOperator. Le code du projet est monté dans le conteneur à
/opt/airflow/project (ajouté au PYTHONPATH ci-dessous).

Aucune logique métier n'est dupliquée : on réutilise les mêmes fonctions
que le scheduler historique (scheduler.py) et le pipeline CLI (pipeline.py).
"""
from __future__ import annotations

import sys
from pathlib import Path

# Le code du projet est monté ici (voir docker-compose : volume project)
PROJECT_ROOT = Path("/opt/airflow/project")
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Bronze — ingestion d'une source unitaire
# ---------------------------------------------------------------------------

def ingest_source(source: str) -> int:
    """Ingest une source Bronze via le registre de main.py.

    Sources valides : dvf, osm, boundaries, iris, revenus, air_quality, crime.
    Retourne le nombre de lignes ingérées (poussé dans les logs Airflow / XCom).
    """
    import argparse
    from main import _build_task_map

    args = argparse.Namespace(date_min=None, date_max=None)
    task_map = _build_task_map(args)
    if source not in task_map:
        raise ValueError(f"Source inconnue : {source} (valides : {sorted(task_map)})")
    df = task_map[source]()
    return int(len(df)) if df is not None else 0


def ingest_static_indicators() -> None:
    """Ingest les indicateurs Bronze statiques (connectivité, santé, tranquillité, ICAR)."""
    from pipeline import _run_static_indicators
    _run_static_indicators()


def ingest_mobility_once() -> int:
    """Exécute un batch micro Vélib' + PRIM (mobilité temps quasi-réel)."""
    from src.ingestion.mobility_micro_batch import run_once
    df_v, df_p = run_once()
    return int(len(df_v)) + int(len(df_p))


# ---------------------------------------------------------------------------
# Silver / Gold / Export
# ---------------------------------------------------------------------------

def run_silver() -> None:
    """Construit la couche Silver (arrondissement + IRIS + scoring)."""
    from src.silver.aggregation import build_silver_layer
    build_silver_layer()


def run_gold() -> None:
    """Construit la couche Gold (tables arrondissement + IRIS + géométrie)."""
    from src.gold.build import build_gold_layer
    build_gold_layer()


def run_export_pg() -> None:
    """Exporte les tables Gold (dont gold_iris_summary) vers PostgreSQL."""
    import os
    from src.gold.export_pg import export_all
    export_all(db_url=os.environ.get("DATABASE_URL"))
