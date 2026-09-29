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
from etl.performance import StageTimer

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
    """Use Airflow's logical date, which is explicit and not worker-local."""
    from etl.ingestion_date import resolve_ingestion_date

    return resolve_ingestion_date(date.fromisoformat(ds))


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


def _development_policy_from_xcom(ti):
    """Read the bounded-run policy selected when this DAG run took its lock."""
    from etl.development_ingestion import (
        DevelopmentIngestionPolicy,
        from_environment as development_policy_from_environment,
    )

    data = ti.xcom_pull(task_ids="check_file_changed", key="development_policy")
    if data is None:
        # Compatibility for direct task invocation in tests and operator tools.
        # Normal DAG runs always persist the policy in check_file_changed.
        return development_policy_from_environment()
    return DevelopmentIngestionPolicy(
        chain_keys=tuple(tuple(key) for key in data["chain_keys"]),
        max_product_rows=data["max_product_rows"],
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


def _renew_ingestion_lock(conn, ti) -> None:
    """Refresh the lock at task boundaries and during product streaming."""
    from etl.ingestion_lock import renew_ingestion_lock

    try:
        lock = _lock_from_xcom(ti)
    except RuntimeError:
        # Direct callable tests and pre-lock failures have no reservation.
        return
    renew_ingestion_lock(conn, lock)


def _fail_active_timer(timer: StageTimer | None, exc: Exception) -> None:
    """Emit a terminal measurement when a task fails after a stage starts."""
    if timer is not None and timer.is_running:
        timer.fail(error_type=type(exc).__name__)


# ---------------------------------------------------------------------------
# Task callables
# ---------------------------------------------------------------------------


def download_zip(ds: str, **kwargs):
    """Download daily SEPA ZIP and register in raw_files."""
    from etl.extract import downloader

    conn = _db_connect()
    timer: StageTimer | None = None
    try:
        timer = StageTimer(log, "download")
        timer.start()
        raw_dir = Path(Variable.get("sepa_raw_dir", default_var="/opt/airflow/data"))
        raw_dir.mkdir(parents=True, exist_ok=True)

        fecha_logica = _run_date(ds)
        dl = downloader.download(conn, raw_dir, fecha_logica)
        timer.finish(filename=dl.filename, size_bytes=dl.size_bytes)

        return {
            "raw_file_id": dl.raw_file_id,
            "filename": dl.filename,
            "path": str(dl.path),
            "sha256": dl.sha256,
            "size_bytes": dl.size_bytes,
            "fecha_datos": str(dl.fecha_datos),
            "fecha_solicitada": str(dl.fecha_datos),
            "fecha_logica": str(fecha_logica),
            "is_new": dl.is_new,
        }
    except Exception as exc:
        _fail_active_timer(timer, exc)
        raise
    finally:
        conn.close()


def check_file_changed(**kwargs):
    """Short-circuit only files that have already been published successfully."""
    from etl.ingestion_lock import acquire_ingestion_lock, release_ingestion_lock
    from etl.ingestion_run import should_process_raw_file
    from etl.retention_policy import from_environment
    from etl.development_ingestion import from_environment as development_policy_from_environment

    ti = kwargs["ti"]
    dl_info = ti.xcom_pull(task_ids="download_zip")

    # Fail before reserving the date if a destructive retention setting is
    # malformed. Every task later reads the same process environment.
    from_environment()
    development_policy = development_policy_from_environment()

    conn = _db_connect()
    lock = None
    try:
        should_process = should_process_raw_file(
            conn, dl_info["raw_file_id"], partial=development_policy.enabled
        )
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
        should_process = should_process_raw_file(
            conn, dl_info["raw_file_id"], partial=development_policy.enabled
        )
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
        ti.xcom_push(
            key="development_policy",
            value={
                "chain_keys": [list(key) for key in development_policy.chain_keys],
                "max_product_rows": development_policy.max_product_rows,
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
    from etl.ingestion_run import open_ingestion_run, record_source_date
    from etl.fact_chunking import from_environment as fact_chunk_size_from_environment
    from etl.regional_scope import scope_from_environment
    from etl.staging import loader as staging_loader
    from etl.storage_metrics import collect_storage_footprint
    from etl.source_archive import inspect_source_archive_date, validate_source_archive_date

    ti = kwargs["ti"]
    dl_info = ti.xcom_pull(task_ids="download_zip")
    development_policy = _development_policy_from_xcom(ti)
    fact_chunk_size = fact_chunk_size_from_environment()
    regional_scope = scope_from_environment()

    conn = _db_connect()
    run_id: int | None = None
    timer: StageTimer | None = None
    try:
        _renew_ingestion_lock(conn, ti)
        fecha_solicitada = dl_info.get("fecha_solicitada")
        fecha_logica = dl_info.get("fecha_logica")
        run_id = open_ingestion_run(
            conn,
            dl_info["raw_file_id"],
            fecha_solicitada=(
                date.fromisoformat(fecha_solicitada)
                if fecha_solicitada is not None
                else None
            ),
            fecha_logica=(
                date.fromisoformat(fecha_logica) if fecha_logica is not None else None
            ),
            development_policy=development_policy,
            fact_chunk_size=fact_chunk_size,
        )
        fecha_fuente = inspect_source_archive_date(dl_info["path"])
        record_source_date(conn, run_id, fecha_fuente)
        fecha_esperada = fecha_logica or dl_info.get("fecha_datos")
        if fecha_esperada is not None:
            validate_source_archive_date(
                fecha_logica=date.fromisoformat(fecha_esperada),
                fecha_fuente=fecha_fuente,
            )

        timer = StageTimer(log.bind(run_id=run_id), "staging")
        timer.start()
        stg = staging_loader.load(
            conn,
            Path(dl_info["path"]),
            run_id,
            heartbeat=lambda: _renew_ingestion_lock(conn, ti),
            development_policy=development_policy,
            regional_scope=regional_scope,
        )
        staging_loader.register_counts(conn, run_id, stg)
        conn.commit()

        timer.finish(
            inner_zips=stg.inner_zips_count,
            rows_comercios=stg.rows_comercios,
            rows_sucursales=stg.rows_sucursales,
            rows_productos=stg.rows_productos,
            rows_productos_source=stg.rows_productos_source,
            rows_productos_selected=stg.rows_productos_selected,
            regional_scope=stg.regional_scope,
            rows_sucursales_out_of_scope=stg.rows_sucursales_out_of_scope,
            rows_sucursales_unlocated=stg.rows_sucursales_unlocated,
            rows_productos_out_of_scope=stg.rows_productos_out_of_scope,
        )
        log.info(
            "airflow.storage_footprint",
            run_id=run_id,
            stage="staging",
            **collect_storage_footprint(conn, run_id=run_id).__dict__,
        )

        return {
            "run_id": run_id,
            "inner_zips_count": stg.inner_zips_count,
            "rows_comercios": stg.rows_comercios,
            "rows_sucursales": stg.rows_sucursales,
            "rows_productos": stg.rows_productos,
            "rows_productos_source": stg.rows_productos_source,
            "rows_productos_selected": stg.rows_productos_selected,
            "rows_productos_out_of_scope": stg.rows_productos_out_of_scope,
        }
    except Exception as exc:
        _fail_active_timer(timer, exc)
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
    timer: StageTimer | None = None
    try:
        _renew_ingestion_lock(conn, ti)
        timer = StageTimer(log.bind(run_id=run_id), "quality")
        timer.start()
        try:
            check_results = quality_checks.run_all(conn, run_id)
        except quality_checks.QualityCheckError as exc:
            reporters.report(results=exc.failures, run_id=run_id, conn=conn)
            conn.commit()
            timer.fail(
                reason="critical_quality_failure",
                failures=[result.name for result in exc.failures],
            )
            raise

        qr = reporters.report(results=check_results, run_id=run_id, conn=conn)
        conn.commit()

        timer.finish(
            passed=qr.passed,
            warnings_failed=qr.warnings_failed,
        )
    except quality_checks.QualityCheckError:
        raise
    except Exception as exc:
        _fail_active_timer(timer, exc)
        _mark_task_failure(conn, run_id, exc)
        raise
    finally:
        conn.close()


def load_dimensions(**kwargs):
    """Load dimensions from staging without materializing all products."""
    from etl.ingestion_processing import load_staged_dimensions
    from etl.ingestion_run import is_partial_run

    ti = kwargs["ti"]
    stg_info = ti.xcom_pull(task_ids="load_staging")
    run_id = stg_info["run_id"]

    conn = _db_connect()
    timer: StageTimer | None = None
    try:
        _renew_ingestion_lock(conn, ti)
        is_partial = is_partial_run(conn, run_id)
        timer = StageTimer(log.bind(run_id=run_id), "dimensions")
        timer.start()
        result = load_staged_dimensions(
            conn, run_id, is_partial=is_partial
        )
        conn.commit()
        timer.finish(
            rows_comercios=result.rows_comercios,
            rows_sucursales=result.rows_sucursales,
            rows_productos=result.rows_productos,
            rows_rejected=result.rows_rejected,
        )
        return {
            "rows_clean_comercios": result.rows_comercios,
            "rows_clean_sucursales": result.rows_sucursales,
            "rows_rejected": result.rows_rejected,
        }
    except Exception as exc:
        _fail_active_timer(timer, exc)
        _mark_task_failure(conn, run_id, exc)
        raise
    finally:
        conn.close()


def load_pending_fact_precios(**kwargs):
    """Stream staging products into the run-local pending table."""
    from etl.fact_chunking import from_run as fact_chunk_size_from_run
    from etl.fact_load_metrics import record_fact_load_metrics
    from etl.ingestion_processing import stream_pending_product_facts

    ti = kwargs["ti"]
    stg_info = ti.xcom_pull(task_ids="load_staging")
    dl_info = ti.xcom_pull(task_ids="download_zip")
    run_id = stg_info["run_id"]
    run_date = date.fromisoformat(dl_info["fecha_datos"])

    conn = _db_connect()
    timer: StageTimer | None = None
    try:
        _renew_ingestion_lock(conn, ti)
        chunk_size = fact_chunk_size_from_run(conn, run_id)
        timer = StageTimer(log.bind(run_id=run_id), "fact_load")
        timer.start(chunk_size=chunk_size)
        result = stream_pending_product_facts(
            conn,
            _db_connect,
            run_id=run_id,
            run_date=run_date,
            chunk_size=chunk_size,
            heartbeat=lambda: _renew_ingestion_lock(conn, ti),
        )
        fact_load_elapsed_seconds = timer.finish(
            chunks=result.chunks,
            chunk_size=chunk_size,
            rows_fact=result.rows_fact,
            rows_valid=result.rows_valid,
            rows_rejected=result.rows_rejected,
            rows_without_keys=result.rows_without_keys,
            rows_deduplicated=result.rows_deduplicated,
        )
        record_fact_load_metrics(
            conn,
            run_id=run_id,
            elapsed_seconds=fact_load_elapsed_seconds,
            chunks=result.chunks,
        )
        conn.commit()
        return {
            "chunks": result.chunks,
            "rows_fact": result.rows_fact,
            "rows_valid": result.rows_valid,
            "rows_rejected": result.rows_rejected,
            "rows_without_keys": result.rows_without_keys,
            "rows_deduplicated": result.rows_deduplicated,
        }
    except Exception as exc:
        _fail_active_timer(timer, exc)
        _mark_task_failure(conn, run_id, exc)
        raise
    finally:
        conn.close()


def publish_daily_snapshot(**kwargs):
    """Atomically publish facts, aggregates and the completed run state."""
    from etl.fact_retention import apply_fact_retention
    from etl.ingestion_lock import release_ingestion_lock
    from etl.ingestion_run import is_partial_run
    from etl.load.publisher import complete_partial_run, publish_pending_facts
    from etl.raw_archive import (
        archive_completed_raw_files,
        archive_dir_from_environment,
    )
    from etl.raw_retention import apply_raw_retention
    from etl.retention_policy import from_environment
    from etl.staging_retention import apply_staging_retention
    from etl.storage_metrics import collect_storage_footprint

    ti = kwargs["ti"]
    stg_info = ti.xcom_pull(task_ids="load_staging")
    pending_info = ti.xcom_pull(task_ids="load_pending_fact_precios")
    dl_info = ti.xcom_pull(task_ids="download_zip")
    run_id = stg_info["run_id"]
    fecha = date.fromisoformat(dl_info["fecha_datos"])
    fecha_id = int(fecha.strftime("%Y%m%d"))
    from etl.reconciliation import ProductReconciliation

    conn = _db_connect()
    timer: StageTimer | None = None
    try:
        _renew_ingestion_lock(conn, ti)
        is_partial = is_partial_run(conn, run_id)
        reconciliation = ProductReconciliation(
            rows_valid=pending_info["rows_valid"],
            rows_rejected=pending_info["rows_rejected"],
            rows_without_keys=pending_info["rows_without_keys"],
            rows_deduplicated=pending_info["rows_deduplicated"],
            rows_fact_pending=pending_info["rows_fact"],
            rows_fact_published=(0 if is_partial else pending_info["rows_fact"]),
            is_partial=is_partial,
        )
        timer = StageTimer(log.bind(run_id=run_id), "publication")
        timer.start()
        if is_partial:
            publication = complete_partial_run(
                conn, run_id=run_id, reconciliation=reconciliation
            )
        else:
            publication = publish_pending_facts(
                conn,
                run_id=run_id,
                fecha_id=fecha_id,
                reconciliation=reconciliation,
            )
        conn.commit()
        timer.finish(
            rows_fact=publication.rows_fact,
            rows_aggregated=publication.rows_aggregated,
            is_partial=is_partial,
            rows_fact_pending=reconciliation.rows_fact_pending,
            rows_fact_published=reconciliation.rows_fact_published,
            rows_valid=reconciliation.rows_valid,
            rows_rejected=reconciliation.rows_rejected,
            rows_without_keys=reconciliation.rows_without_keys,
            rows_deduplicated=reconciliation.rows_deduplicated,
        )
        log.info(
            "airflow.storage_footprint",
            run_id=run_id,
            stage="publication",
            **collect_storage_footprint(conn, run_id=run_id).__dict__,
        )
        if is_partial:
            log.info(
                "airflow.partial_run.completed",
                run_id=run_id,
                rows_fact_pending=reconciliation.rows_fact_pending,
                rows_fact_published=0,
                retention_skipped=True,
            )
            release_ingestion_lock(conn, _lock_from_xcom(ti))
            return
        raw_dir = Path(Variable.get("sepa_raw_dir", default_var="/opt/airflow/data"))
        try:
            archive_result = archive_completed_raw_files(
                conn,
                raw_dir=raw_dir,
                archive_dir=archive_dir_from_environment(raw_dir),
            )
            log.info(
                "airflow.raw_archive.completed", run_id=run_id, **archive_result.__dict__
            )
        except Exception:
            # Archiving is retried by the next run; it must not fail publication.
            conn.rollback()
            log.exception("airflow.raw_archive.failed", run_id=run_id)
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
                    aggregate_rows_deleted=retention_result.aggregate_rows_deleted,
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
            try:
                raw_retention_result = apply_raw_retention(
                    conn,
                    raw_dir=raw_dir,
                    archive_dir=archive_dir_from_environment(raw_dir),
                    policy=retention_policy,
                    fecha_datos=fecha,
                )
                if raw_retention_result is not None:
                    log.info(
                        "airflow.raw_retention.completed",
                        cutoff=str(retention_policy.raw_cutoff(fecha)),
                        deleted_file_ids=raw_retention_result.deleted_file_ids,
                        missing_file_ids=raw_retention_result.missing_file_ids,
                        archive_missing_file_ids=raw_retention_result.archive_missing_file_ids,
                    )
            except Exception:
                # Raw ZIP cleanup is last and must not prevent lock release.
                conn.rollback()
                log.exception("airflow.raw_retention.failed", run_id=run_id)
        release_ingestion_lock(conn, _lock_from_xcom(ti))
        log.info("airflow.ingestion_lock.released", fecha=str(fecha))
    except Exception as exc:
        _fail_active_timer(timer, exc)
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
