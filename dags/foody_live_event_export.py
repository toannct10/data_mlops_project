"""Optional S3-only outbox publication; RAW/dbt publication remains a separate gate."""

import os
from datetime import timedelta

import pendulum
from airflow import DAG
from src.monitoring.cloud_pipeline import task_success, task_failure
from airflow.operators.python import PythonOperator


def publish_events():
    from src.ordering.event_export import export_pending
    from src.warehouse.loader import connection

    with connection() as conn:
        result = export_pending(conn, limit=100)
    # Commit attempt accounting before asking Airflow to retry a failed batch.
    if result["status"] == "FAILED":
        raise RuntimeError("S3 event publication failed; events remain pending")
    return result


interval = max(30, int(os.getenv("FOODY_LIVE_SYNC_INTERVAL_SECONDS", "60")))
with DAG(
    dag_id="foody_live_event_export",
    description="Opt-in incremental outbox to S3; no Snowflake acceptance implied",
    start_date=pendulum.datetime(2026, 11, 20, tz="Asia/Ho_Chi_Minh"),
    schedule=timedelta(seconds=interval)
    if os.getenv("FOODY_LIVE_EVENT_SYNC_ENABLED", "false").lower() == "true"
    else None,
    catchup=False,
    is_paused_upon_creation=os.getenv('FOODY_LIVE_EVENT_SYNC_ENABLED', 'false').lower() != 'true',
    max_active_runs=1,
    default_args={
        "owner": "foody",
        "on_success_callback": task_success,
        "on_failure_callback": task_failure,
        "retries": 2,
        "retry_delay": timedelta(seconds=30),
        "execution_timeout": timedelta(minutes=5),
    },
    tags=["foody", "cloud", "optional"],
) as dag:
    PythonOperator(task_id="publish_committed_events", python_callable=publish_events)
