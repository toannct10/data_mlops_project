from datetime import datetime, timedelta
import subprocess
import os
import sys

from airflow import DAG
from src.monitoring.cloud_pipeline import task_success, task_failure
from airflow.operators.python import PythonOperator

from src.cloud_incremental import (
    load_incremental_events,
    verify_cloud_quality,
)


DBT_PROJECT = "/app/dbt_foody/foody_analytics"


def run_dbt_build():
    command = [
        sys.executable,
        "-m",
        "scripts.run_restricted_dbt",
        "build",
        "--project-dir",
        DBT_PROJECT,
        "--profiles-dir",
        DBT_PROJECT,
        "--target",
        "prod",
    ]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=600,
    )

    print(result.stdout)

    if result.stderr:
        print(result.stderr)

    if result.returncode != 0:
        raise RuntimeError(
            f"dbt build failed with exit code {result.returncode}"
        )

    return {
        "status": "SUCCESS",
        "returncode": result.returncode,
    }


def quality_gate():
    return verify_cloud_quality()


default_args = {
    "owner": "foody",
    "on_success_callback": task_success,
    "on_failure_callback": task_failure,
    "retries": 2,
    "retry_delay": timedelta(minutes=1),
}


with DAG(
    dag_id="foody_cloud_incremental",
    description=(
        "S3 immutable live events -> Snowflake RAW "
        "MERGE -> dbt STAGING/MART"
    ),
    default_args=default_args,
    start_date=datetime(2026, 11, 22),
    schedule=timedelta(minutes=2) if os.getenv('FOODY_CLOUD_INCREMENTAL_ENABLED', 'false').lower() == 'true' else None,
    max_active_runs=1,
    is_paused_upon_creation=os.getenv('FOODY_CLOUD_INCREMENTAL_ENABLED', 'false').lower() != 'true',
    catchup=False,
    tags=[
        "foody",
        "cloud",
        "incremental",
        "snowflake",
        "dbt",
    ],
) as dag:

    load_snowflake_incremental = PythonOperator(
        task_id="load_snowflake_incremental",
        python_callable=load_incremental_events,
    )

    dbt_build = PythonOperator(
        task_id="dbt_build",
        python_callable=run_dbt_build,
    )

    quality_gate_task = PythonOperator(
        task_id="quality_gate",
        python_callable=quality_gate,
    )

    (
        load_snowflake_incremental
        >> dbt_build
        >> quality_gate_task
    )
