"""
DAG — Pipeline Medallion complet (quotidien)
============================================
Remplace `job_full_pipeline` du scheduler historique (scheduler.py).

Graphe :
  [ingest_boundaries, ingest_iris, ingest_dvf, ingest_osm, ingest_revenus,
   ingest_crime, ingest_air_quality, ingest_static_indicators, ingest_mobility]
        >> build_silver >> build_gold >> export_postgres

Toutes les sources Bronze s'exécutent en parallèle (LocalExecutor), puis les
couches Silver → Gold → PostgreSQL s'enchaînent séquentiellement.
La maille IRIS (source `iris`) est ingérée au même titre que les autres.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.python import PythonOperator

from _pipeline_tasks import (
    ingest_source,
    ingest_static_indicators,
    ingest_mobility_once,
    run_silver,
    run_gold,
    run_export_pg,
)

default_args = {
    "owner": "data-eng",
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
}

# Sources Bronze ingérées en parallèle (la nouvelle source `iris` y figure)
BRONZE_SOURCES = [
    "boundaries", "iris", "dvf", "osm", "revenus", "crime", "air_quality",
]

with DAG(
    dag_id="urban_data_pipeline",
    description="Pipeline Medallion Bronze→Silver→Gold→PostgreSQL (maille IRIS)",
    default_args=default_args,
    schedule="@daily",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    max_active_runs=1,
    tags=["medallion", "iris", "bronze", "silver", "gold"],
) as dag:

    bronze_tasks = [
        PythonOperator(
            task_id=f"ingest_{source}",
            python_callable=ingest_source,
            op_args=[source],
        )
        for source in BRONZE_SOURCES
    ]

    static_indicators = PythonOperator(
        task_id="ingest_static_indicators",
        python_callable=ingest_static_indicators,
    )

    mobility = PythonOperator(
        task_id="ingest_mobility_once",
        python_callable=ingest_mobility_once,
    )

    silver = PythonOperator(task_id="build_silver", python_callable=run_silver)
    gold = PythonOperator(task_id="build_gold", python_callable=run_gold)
    export = PythonOperator(task_id="export_postgres", python_callable=run_export_pg)

    # Toutes les ingestions Bronze >> Silver >> Gold >> Export PG
    [*bronze_tasks, static_indicators, mobility] >> silver >> gold >> export
