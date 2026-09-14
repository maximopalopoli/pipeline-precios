"""
dag_sepa_ingest.py — Airflow DAG for daily SEPA price ingestion.

Schedule: 06:30 and 10:30 daily (two runs to capture rectified prices).
Dimensions are loaded from staging and products are streamed in chunks into
pending facts before the daily snapshot is published atomically.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from pathlib import Path

import psycopg2
import structlog

from airflow import DAG
from airflow.models import Variable
from airflow.operators.python import PythonOperator, ShortCircuitOperator

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _db_connect() -> psycopg2.extensions.connection:
    """Connect using Airflow's postgres_default connection env vars."""
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    )


def _run_date(ds: str) -> date:
    return date.fromisoformat(ds)


# ---------------------------------------------------------------------------
# Task callables
# ---------------------------------------------------------------------------


def download_zip(ds: str, **kwargs):
    """Download daily SEPA ZIP and register in raw_files."""
    from etl.extract import downloader

    conn = _db_connect()
    try:
        raw_dir = Path(Variable.get("sepa_raw_dir", default_var="/opt/airflow/data"))
        raw_dir.mkdir(parents=True, exist_ok=True)

        dl = downloader.download(conn, raw_dir, _run_date(ds))

        return {
            "raw_file_id": dl.raw_file_id,
            "filename": dl.filename,
            "path": str(dl.path),
            "sha256": dl.sha256,
            "size_bytes": dl.size_bytes,
            "fecha_datos": str(dl.fecha_datos),
            "is_new": dl.is_new,
        }
    finally:
        conn.close()


def check_file_changed(**kwargs):
    """Short-circuit only files that have already been published successfully."""
    from etl.ingestion_run import should_process_raw_file

    ti = kwargs["ti"]
    dl_info = ti.xcom_pull(task_ids="download_zip")

    conn = _db_connect()
    try:
        should_process = should_process_raw_file(conn, dl_info["raw_file_id"])
        log.info(
            "download.duplicate_decision",
            raw_file_id=dl_info["raw_file_id"],
            is_new=dl_info["is_new"],
            should_process=should_process,
        )
        return should_process
    finally:
        conn.close()


def load_staging(**kwargs):
    """Open ingestion run + load staging tables from ZIP."""
    from etl.ingestion_run import open_ingestion_run
    from etl.staging import loader as staging_loader

    ti = kwargs["ti"]
    dl_info = ti.xcom_pull(task_ids="download_zip")

    conn = _db_connect()
    try:
        run_id = open_ingestion_run(conn, dl_info["raw_file_id"])

        # Load staging
        stg = staging_loader.load(conn, Path(dl_info["path"]), run_id)
        staging_loader.register_counts(conn, run_id, stg)
        conn.commit()

        log.info(
            "staging.loaded",
            run_id=run_id,
            inner_zips=stg.inner_zips_count,
            rows_productos=stg.rows_productos,
        )

        return {
            "run_id": run_id,
            "inner_zips_count": stg.inner_zips_count,
            "rows_comercios": stg.rows_comercios,
            "rows_sucursales": stg.rows_sucursales,
            "rows_productos": stg.rows_productos,
        }
    finally:
        conn.close()


def run_quality_checks(**kwargs):
    """Run critical + warning quality checks. Abort on critical failure."""
    from etl.quality import checks as quality_checks
    from etl.quality import reporters

    ti = kwargs["ti"]
    stg_info = ti.xcom_pull(task_ids="load_staging")
    run_id = stg_info["run_id"]

    conn = _db_connect()
    try:
        try:
            check_results = quality_checks.run_all(conn, run_id)
        except quality_checks.QualityCheckError as exc:
            reporters.report(results=exc.failures, run_id=run_id, conn=conn)
            conn.commit()
            raise

        qr = reporters.report(results=check_results, run_id=run_id, conn=conn)
        conn.commit()

        log.info(
            "quality.done",
            run_id=run_id,
            passed=qr.passed,
            warnings_failed=qr.warnings_failed,
        )
    finally:
        conn.close()


def load_dimensions(**kwargs):
    """Load dimensions from staging without materializing all products."""
    from etl.ingestion_processing import load_staged_dimensions

    ti = kwargs["ti"]
    stg_info = ti.xcom_pull(task_ids="load_staging")
    run_id = stg_info["run_id"]

    conn = _db_connect()
    try:
        result = load_staged_dimensions(conn, run_id)
        conn.commit()
        log.info(
            "warehouse.dimensions_loaded",
            run_id=run_id,
            comercios=result.rows_comercios,
            sucursales=result.rows_sucursales,
            productos=result.rows_productos,
        )
        return {
            "rows_clean_comercios": result.rows_comercios,
            "rows_clean_sucursales": result.rows_sucursales,
            "rows_rejected": result.rows_rejected,
        }
    finally:
        conn.close()


def load_pending_fact_precios(**kwargs):
    """Stream staging products into the run-local pending table."""
    from etl.ingestion_processing import stream_pending_product_facts

    ti = kwargs["ti"]
    stg_info = ti.xcom_pull(task_ids="load_staging")
    dl_info = ti.xcom_pull(task_ids="download_zip")
    run_id = stg_info["run_id"]
    run_date = date.fromisoformat(dl_info["fecha_datos"])

    conn = _db_connect()
    try:
        result = stream_pending_product_facts(
            conn,
            _db_connect,
            run_id=run_id,
            run_date=run_date,
        )
        conn.commit()
        log.info(
            "warehouse.pending_fact_loaded",
            run_id=run_id,
            chunks=result.chunks,
            rows=result.rows_fact,
        )
        return {
            "chunks": result.chunks,
            "rows_fact": result.rows_fact,
            "rows_valid": result.rows_valid,
        }
    finally:
        conn.close()


def publish_daily_snapshot(**kwargs):
    """Atomically publish facts, aggregates and the completed run state."""
    from etl.load.publisher import publish_pending_facts
    from etl.quality import reporters

    ti = kwargs["ti"]
    stg_info = ti.xcom_pull(task_ids="load_staging")
    pending_info = ti.xcom_pull(task_ids="load_pending_fact_precios")
    dl_info = ti.xcom_pull(task_ids="download_zip")
    run_id = stg_info["run_id"]
    fecha = date.fromisoformat(dl_info["fecha_datos"])
    fecha_id = int(fecha.strftime("%Y%m%d"))
    rows_valid = pending_info["rows_valid"]

    conn = _db_connect()
    try:
        publication = publish_pending_facts(
            conn,
            run_id=run_id,
            fecha_id=fecha_id,
            rows_valid=rows_valid,
        )
        conn.commit()
        log.info(
            "publication.done",
            run_id=run_id,
            fecha_id=fecha_id,
            rows_fact=publication.rows_fact,
            rows_aggregated=publication.rows_aggregated,
        )
    except Exception as exc:
        conn.rollback()
        reporters.mark_run_failed(conn, run_id, str(exc))
        raise
    finally:
        conn.close()

# ---------------------------------------------------------------------------
# DAG definition
# ---------------------------------------------------------------------------

default_args = {
    "owner": "sepa",
    "retries": 2,
    "retry_delay": timedelta(minutes=15),
    "execution_timeout": timedelta(minutes=30),
}

with DAG(
    dag_id="sepa_ingest",
    default_args=default_args,
    description="Daily SEPA price ingestion pipeline",
    schedule="30 6,10 * * *",
    start_date=datetime(2026, 3, 19),
    catchup=False,
    max_active_runs=1,
    tags=["sepa", "etl"],
) as dag:

    t_download = PythonOperator(
        task_id="download_zip",
        python_callable=download_zip,
    )

    t_check = ShortCircuitOperator(
        task_id="check_file_changed",
        python_callable=check_file_changed,
    )

    t_staging = PythonOperator(
        task_id="load_staging",
        python_callable=load_staging,
    )

    t_quality = PythonOperator(
        task_id="run_quality_checks",
        python_callable=run_quality_checks,
    )

    t_dims = PythonOperator(
        task_id="load_dimensions",
        python_callable=load_dimensions,
    )

    t_pending_fact = PythonOperator(
        task_id="load_pending_fact_precios",
        python_callable=load_pending_fact_precios,
    )

    t_publish = PythonOperator(
        task_id="publish_daily_snapshot",
        python_callable=publish_daily_snapshot,
    )

    # Sequential chain
    (
        t_download
        >> t_check
        >> t_staging
        >> t_quality
        >> t_dims
        >> t_pending_fact
        >> t_publish
    )
