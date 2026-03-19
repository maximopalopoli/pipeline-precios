"""
dag_sepa_ingest.py — Airflow DAG for daily SEPA price ingestion.

Schedule: 06:30 and 10:30 daily (two runs to capture rectified prices).
"""

from __future__ import annotations

import os
import pickle
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import psycopg2
import structlog

from airflow import DAG
from airflow.models import Variable
from airflow.operators.python import PythonOperator, ShortCircuitOperator

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TEMP_DIR = Path(tempfile.gettempdir()) / "sepa_airflow"


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


def _temp_path(run_id: int, name: str) -> Path:
    d = _TEMP_DIR / str(run_id)
    d.mkdir(parents=True, exist_ok=True)
    return d / name


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
    """ShortCircuit: skip downstream tasks if file hash already existed."""
    ti = kwargs["ti"]
    dl_info = ti.xcom_pull(task_ids="download_zip")
    return dl_info["is_new"]


def load_staging(**kwargs):
    """Open ingestion run + load staging tables from ZIP."""
    from etl.staging import loader as staging_loader

    ti = kwargs["ti"]
    dl_info = ti.xcom_pull(task_ids="download_zip")

    conn = _db_connect()
    try:
        # Open run
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ingestion_runs (raw_file_id, status) "
                "VALUES (%s, 'running') RETURNING id",
                (dl_info["raw_file_id"],),
            )
            run_id = cur.fetchone()[0]
        conn.commit()

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


def transform_and_resolve(**kwargs):
    """Clean staging rows, resolve dimensions, build fact DataFrame."""
    from etl.transform.cleaner import (
        clean_comercios,
        clean_productos,
        clean_sucursales,
    )
    from etl.transform.fact_builder import build_fact_df

    ti = kwargs["ti"]
    stg_info = ti.xcom_pull(task_ids="load_staging")
    dl_info = ti.xcom_pull(task_ids="download_zip")
    run_id = stg_info["run_id"]
    run_date = date.fromisoformat(dl_info["fecha_datos"])

    conn = _db_connect()
    try:
        # Read staging
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM stg_comercios WHERE run_id = %s", (run_id,))
            df_c = pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])

            cur.execute("SELECT * FROM stg_sucursales WHERE run_id = %s", (run_id,))
            df_s = pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])

            cur.execute("SELECT * FROM stg_productos WHERE run_id = %s", (run_id,))
            df_p = pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])

        # Clean
        clean_c = clean_comercios(df_c)
        clean_s = clean_sucursales(df_s)
        clean_p = clean_productos(df_p)

        total_rejected = (
            len(clean_c.rejected) + len(clean_s.rejected) + len(clean_p.rejected)
        )
        log.info(
            "transform.cleaned",
            run_id=run_id,
            comercios=len(clean_c.clean),
            sucursales=len(clean_s.clean),
            productos=len(clean_p.clean),
            rejected=total_rejected,
        )

        # Build fact
        fact_result = build_fact_df(
            conn,
            productos=clean_p.clean,
            sucursales=clean_s.clean,
            comercios=clean_c.clean,
            run_date=run_date,
            run_id=run_id,
        )

        log.info(
            "transform.fact_built",
            run_id=run_id,
            fact_rows=len(fact_result.df),
            fact_rejected=len(fact_result.rejected),
        )

        # Persist intermediate results for downstream tasks
        pickle.dump(clean_c.clean, open(_temp_path(run_id, "comercios.pkl"), "wb"))
        pickle.dump(clean_s.clean, open(_temp_path(run_id, "sucursales.pkl"), "wb"))
        pickle.dump(clean_p.clean, open(_temp_path(run_id, "productos.pkl"), "wb"))
        pickle.dump(fact_result.df, open(_temp_path(run_id, "fact_df.pkl"), "wb"))

        return {
            "rows_clean_comercios": len(clean_c.clean),
            "rows_clean_sucursales": len(clean_s.clean),
            "rows_clean_productos": len(clean_p.clean),
            "rows_fact": len(fact_result.df),
            "total_rejected": total_rejected,
        }
    finally:
        conn.close()


def load_dimensions(**kwargs):
    """Bulk-upsert dimension tables from cleaned data."""
    from etl.load.warehouse_loader import load_dimensions as wh_load_dims

    ti = kwargs["ti"]
    stg_info = ti.xcom_pull(task_ids="load_staging")
    run_id = stg_info["run_id"]

    comercios = pickle.load(open(_temp_path(run_id, "comercios.pkl"), "rb"))
    sucursales = pickle.load(open(_temp_path(run_id, "sucursales.pkl"), "rb"))
    productos = pickle.load(open(_temp_path(run_id, "productos.pkl"), "rb"))

    conn = _db_connect()
    try:
        rc, rs, rp = wh_load_dims(conn, comercios, sucursales, productos)
        conn.commit()
        log.info(
            "warehouse.dimensions_loaded",
            run_id=run_id,
            comercios=rc,
            sucursales=rs,
            productos=rp,
        )
    finally:
        conn.close()


def load_fact_precios(**kwargs):
    """COPY fact_precios from built DataFrame."""
    from etl.load.warehouse_loader import load_fact, register_rows_loaded

    ti = kwargs["ti"]
    stg_info = ti.xcom_pull(task_ids="load_staging")
    run_id = stg_info["run_id"]

    fact_df = pickle.load(open(_temp_path(run_id, "fact_df.pkl"), "rb"))

    conn = _db_connect()
    try:
        rows = load_fact(conn, fact_df, run_id)
        register_rows_loaded(conn, run_id, rows)
        conn.commit()
        log.info("warehouse.fact_loaded", run_id=run_id, rows=rows)
    finally:
        conn.close()


def compute_aggregations(**kwargs):
    """Recompute daily aggregations for the run date."""
    from etl.load.aggregator import aggregate

    ti = kwargs["ti"]
    dl_info = ti.xcom_pull(task_ids="download_zip")
    fecha = date.fromisoformat(dl_info["fecha_datos"])
    fecha_id = int(fecha.strftime("%Y%m%d"))

    conn = _db_connect()
    try:
        agg = aggregate(conn, fecha_id)
        conn.commit()
        log.info(
            "aggregations.done",
            fecha_id=fecha_id,
            rows_upserted=agg.rows_upserted,
        )
    finally:
        conn.close()


def finalize_run(**kwargs):
    """Mark ingestion run as completed and clean up temp files."""
    import shutil

    ti = kwargs["ti"]
    stg_info = ti.xcom_pull(task_ids="load_staging")
    transform_info = ti.xcom_pull(task_ids="transform_and_resolve")
    run_id = stg_info["run_id"]
    rows_valid = transform_info["rows_clean_productos"]

    conn = _db_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE ingestion_runs
                   SET status      = 'completed',
                       finished_at = NOW(),
                       rows_valid  = %s
                 WHERE id = %s
                """,
                (rows_valid, run_id),
            )
        conn.commit()
        log.info("run.finalized", run_id=run_id, rows_valid=rows_valid)
    finally:
        conn.close()

    # Cleanup temp pickle files
    temp_dir = _TEMP_DIR / str(run_id)
    if temp_dir.exists():
        shutil.rmtree(temp_dir)


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

    t_transform = PythonOperator(
        task_id="transform_and_resolve",
        python_callable=transform_and_resolve,
    )

    t_dims = PythonOperator(
        task_id="load_dimensions",
        python_callable=load_dimensions,
    )

    t_fact = PythonOperator(
        task_id="load_fact_precios",
        python_callable=load_fact_precios,
    )

    t_agg = PythonOperator(
        task_id="compute_aggregations",
        python_callable=compute_aggregations,
    )

    t_finalize = PythonOperator(
        task_id="finalize_run",
        python_callable=finalize_run,
    )

    # Sequential chain
    (
        t_download
        >> t_check
        >> t_staging
        >> t_quality
        >> t_transform
        >> t_dims
        >> t_fact
        >> t_agg
        >> t_finalize
    )
