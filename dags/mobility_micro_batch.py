"""
DAG — Mobilité micro-batch Vélib' / PRIM (toutes les 15 min)
============================================================
Remplace le daemon `mobility_micro_batch` lancé par pipeline.py/scheduler.py.

Cadence courte (données temps quasi-réel) : un batch Vélib' + PRIM toutes les
15 minutes. Le recalcul Silver/Gold n'est PAS déclenché ici (trop lourd à cette
fréquence) — la mobilité est rafraîchie dans le DAG quotidien et le DAG horaire.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.python import PythonOperator

from _pipeline_tasks import ingest_mobility_once

default_args = {
    "owner": "data-eng",
    "retries": 1,
    "retry_delay": timedelta(minutes=1),
}

with DAG(
    dag_id="mobility_micro_batch",
    description="Batch micro Vélib' + PRIM (temps quasi-réel)",
    default_args=default_args,
    schedule="*/15 * * * *",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    max_active_runs=1,
    tags=["mobility", "velib", "realtime"],
) as dag:

    PythonOperator(
        task_id="mobility_once",
        python_callable=ingest_mobility_once,
    )
