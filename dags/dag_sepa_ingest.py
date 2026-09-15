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


def _mark_task_failure(conn, run_id: int, exc: Exception) -> None:
    """Persist a task error for an ingestion that has already been opened."""
    from etl.ingestion_run import mark_run_failed

    conn.rollback()
    mark_run_failed(conn, run_id, str(exc))


def _lock_from_xcom(ti):
    """Read the durable date reservation made by ``check_file_changed``."""
    from etl.ingestion_lock import IngestionLock

    data = ti.xcom_pull(task_ids="check_file_changed", key="ingestion_lock")
    if data is None:
        raise RuntimeError("Airflow ingestion lock is missing from XCom.")
    return IngestionLock(
        fecha_datos=date.fromisoformat(data["fecha_datos"]),
        raw_file_id=data["raw_file_id"],
        owner=data["owner"],
        token=data["token"],
        acquired_at=datetime.fromisoformat(data["acquired_at"]),
        expires_at=datetime.fromisoformat(data["expires_at"]),
    )


def _release_ingestion_lock_on_failure(context) -> None:
    """Release the date after Airflow exhausts a task's configured retries."""
    from etl.ingestion_lock import IngestionLockOwnershipError, release_ingestion_lock

    try:
        lock = _lock_from_xcom(context["ti"])
    except RuntimeError:
        # Download/check failures occur before a date can have been reserved.
        return

    conn = _db_connect()
    try:
        release_ingestion_lock(conn, lock)
        log.info("airflow.ingestion_lock.released_after_failure", fecha=str(lock.fecha_datos))
    except IngestionLockOwnershipError:
        log.warning(
            "airflow.ingestion_lock.already_released_after_failure",
            fecha=str(lock.fecha_datos),
        )
    finally:
        conn.close()


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
    from etl.ingestion_lock import acquire_ingestion_lock, release_ingestion_lock
    from etl.ingestion_run import should_process_raw_file
    from etl.retention_policy import from_environment

    ti = kwargs["ti"]
    dl_info = ti.xcom_pull(task_ids="download_zip")

    # Fail before reserving the date if a destructive retention setting is
    # malformed. Every task later reads the same process environment.
    from_environment()

    conn = _db_connect()
    lock = None
    try:
        should_process = should_process_raw_file(conn, dl_info["raw_file_id"])
        if not should_process:
            log.info(
                "download.duplicate_decision",
                raw_file_id=dl_info["raw_file_id"],
                is_new=dl_info["is_new"],
                should_process=False,
            )
            return False

        lock = acquire_ingestion_lock(
            conn,
            fecha_datos=date.fromisoformat(dl_info["fecha_datos"]),
            raw_file_id=dl_info["raw_file_id"],
            owner=f"airflow:{kwargs.get('run_id', 'unknown')}",
        )
        # A CLI execution could have published between the first check and the
        # reservation. Do not start staging when that happened.
        should_process = should_process_raw_file(conn, dl_info["raw_file_id"])
        log.info(
            "download.duplicate_decision",
            raw_file_id=dl_info["raw_file_id"],
            is_new=dl_info["is_new"],
            should_process=should_process,
        )
        if not should_process:
            release_ingestion_lock(conn, lock)
            lock = None
            return False

        ti.xcom_push(
            key="ingestion_lock",
            value={
                "fecha_datos": str(lock.fecha_datos),
                "raw_file_id": lock.raw_file_id,
                "owner": lock.owner,
                "token": lock.token,
                "acquired_at": lock.acquired_at.isoformat(),
                "expires_at": lock.expires_at.isoformat(),
            },
        )
        return should_process
    except Exception:
        if lock is not None:
            release_ingestion_lock(conn, lock)
        raise
    finally:
        conn.close()


def load_staging(**kwargs):
    """Open ingestion run + load staging tables from ZIP."""
    from etl.ingestion_run import open_ingestion_run
    from etl.staging import loader as staging_loader

    ti = kwargs["ti"]
    dl_info = ti.xcom_pull(task_ids="download_zip")

    conn = _db_connect()
    run_id: int | None = None
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
    except Exception as exc:
        if run_id is not None:
            _mark_task_failure(conn, run_id, exc)
        raise
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
    except quality_checks.QualityCheckError:
        raise
    except Exception as exc:
        _mark_task_failure(conn, run_id, exc)
        raise
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
    except Exception as exc:
        _mark_task_failure(conn, run_id, exc)
        raise
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
    except Exception as exc:
        _mark_task_failure(conn, run_id, exc)
        raise
    finally:
        conn.close()


def publish_daily_snapshot(**kwargs):
    """Atomically publish facts, aggregates and the completed run state."""
    from etl.fact_retention import apply_fact_retention
    from etl.ingestion_lock import release_ingestion_lock
    from etl.load.publisher import publish_pending_facts
    from etl.retention_policy import from_environment
    from etl.staging_retention import apply_staging_retention

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
        retention_policy = None
        try:
            retention_policy = from_environment()
            retention_result = apply_fact_retention(
                conn,
                policy=retention_policy,
                fecha_datos=fecha,
            )
            if retention_result is not None:
                log.info(
                    "airflow.fact_retention.completed",
                    cutoff=str(retention_policy.fact_cutoff(fecha)),
                    partitions=retention_result.partitions,
                )
        except Exception:
            # The snapshot is already committed. Do not report a successful
            # publication as failed only because its best-effort cleanup fails.
            conn.rollback()
            log.exception("airflow.fact_retention.failed", run_id=run_id)
        if retention_policy is not None:
            try:
                staging_retention_result = apply_staging_retention(
                    conn,
                    policy=retention_policy,
                    fecha_datos=fecha,
                )
                if staging_retention_result is not None:
                    log.info(
                        "airflow.staging_retention.completed",
                        cutoff=str(retention_policy.staging_cutoff(fecha)),
                        run_ids=staging_retention_result.run_ids,
                    )
            except Exception:
                # Staging cleanup must not prevent fact cleanup or lock release.
                conn.rollback()
                log.exception("airflow.staging_retention.failed", run_id=run_id)
        release_ingestion_lock(conn, _lock_from_xcom(ti))
        log.info("airflow.ingestion_lock.released", fecha=str(fecha))
    except Exception as exc:
        _mark_task_failure(conn, run_id, exc)
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
    "on_failure_callback": _release_ingestion_lock_on_failure,
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
