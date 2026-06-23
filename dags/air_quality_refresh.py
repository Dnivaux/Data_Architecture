"""
DAG — Rafraîchissement qualité de l'air (horaire)
=================================================
Remplace `job_air_quality` du scheduler historique (scheduler.py).

Source très fraîche (Open-Meteo, gratuite, sans quota strict) : on ré-ingère
l'air + pollen toutes les heures, puis on recalcule Silver + Gold pour propager
les nouvelles valeurs (la maille IRIS rediffuse l'air à ses IRIS enfants).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.python import PythonOperator

from _pipeline_tasks import ingest_source, run_silver, run_gold

default_args = {
    "owner": "data-eng",
    "retries": 2,
    "retry_delay": timedelta(minutes=2),
}

with DAG(
    dag_id="air_quality_refresh",
    description="Qualité de l'air + pollen (Open-Meteo) — horaire, propage Silver/Gold",
    default_args=default_args,
    schedule="@hourly",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    max_active_runs=1,
    tags=["air", "open-meteo", "hourly"],
) as dag:

    ingest_air = PythonOperator(
        task_id="ingest_air_quality",
        python_callable=ingest_source,
        op_args=["air_quality"],
    )
    silver = PythonOperator(task_id="build_silver", python_callable=run_silver)
    gold = PythonOperator(task_id="build_gold", python_callable=run_gold)

    ingest_air >> silver >> gold
