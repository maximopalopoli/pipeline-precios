"""
run_pipeline.py — End-to-end SEPA price pipeline.

Execution order
---------------
0.  Ensure reference seeds (dim_provincia, dim_fecha) are loaded
1.  Download daily SEPA ZIP → register in raw_files (skip if SHA-256 already seen)
2.  Open ingestion_run (status = 'running')
3.  Load staging  (stg_comercios / stg_sucursales / stg_productos)
4.  Quality checks (CRITICAL failures abort the run)
5.  Clean and load dimensions from staging
6.  Stream products by chunk into pending facts using dimension caches
7.  Publish facts and aggregates atomically, then mark the run completed

Usage
-----
    python -m etl.run_pipeline
    python -m etl.run_pipeline --fecha 2026-03-15
    python -m etl.run_pipeline --fecha 2026-03-15 --raw-dir /data/sepa

Environment variables
---------------------
    POSTGRES_HOST   (default: localhost)
    POSTGRES_PORT   (default: 5432)
    POSTGRES_DB     (required)
    POSTGRES_USER   (required)
    POSTGRES_PASSWORD (required)
    SEPA_DEV_CHAIN_KEYS / SEPA_DEV_MAX_PRODUCT_ROWS (optional bounded local run)
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date
from pathlib import Path

import psycopg2
import structlog
import structlog.dev

from etl import alerting
from etl.development_ingestion import (
    DevelopmentIngestionPolicy,
    from_environment as development_policy_from_environment,
    with_cli_overrides,
)
from etl.extract import downloader
from etl.fact_retention import apply_fact_retention
from etl.fact_chunking import with_cli_override as fact_chunk_size_with_cli_override
from etl.ingestion_date import resolve_ingestion_date
from etl.ingestion_lock import (
    IngestionLock,
    acquire_ingestion_lock,
    release_ingestion_lock,
    renew_ingestion_lock,
)
from etl.ingestion_run import (
    IngestionResult,
    open_ingestion_run,
    record_source_date,
    should_process_raw_file,
)
from etl.ingestion_processing import (
    load_staged_dimensions,
    reconciliation_for_publication,
    stream_pending_product_facts,
)
from etl.load.publisher import complete_partial_run, publish_pending_facts
from etl.performance import StageTimer, items_per_second, process_max_rss_bytes
from etl.quality import checks as quality_checks
from etl.quality import reporters
from etl.raw_retention import apply_raw_retention
from etl.retention_policy import from_environment
from etl.staging_retention import apply_staging_retention
from etl.staging import loader as staging_loader
from etl.storage_metrics import collect_storage_footprint
from etl.source_archive import inspect_source_archive_date, validate_source_archive_date

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------


def _configure_logging() -> None:
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.BoundLogger,
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
    )


log = structlog.get_logger(__name__)

_SEEDS_DIR = Path(__file__).resolve().parent.parent / "db" / "seeds"


def _ensure_seeds(conn) -> None:
    """Execute seed SQL files if their target tables are empty.

    Seeds are idempotent (ON CONFLICT DO NOTHING) so re-running is safe,
    but we skip execution entirely when tables are already populated to
    avoid unnecessary work on every pipeline run.
    """
    seeds = [
        ("dim_provincia", "seed_provincias.sql"),
        ("dim_fecha", "seed_fechas.sql"),
    ]
    with conn.cursor() as cur:
        for table, filename in seeds:
            cur.execute(f"SELECT EXISTS (SELECT 1 FROM {table} LIMIT 1)")
            has_rows = cur.fetchone()[0]
            if not has_rows:
                seed_path = _SEEDS_DIR / filename
                log.info("pipeline.seed.loading", table=table, file=filename)
                cur.execute(seed_path.read_text())
                log.info("pipeline.seed.loaded", table=table)
    conn.commit()


# ---------------------------------------------------------------------------
# Database connection
# ---------------------------------------------------------------------------


def _db_connect() -> psycopg2.extensions.connection:
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    )


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def run(
    fecha: date | None = None,
    raw_dir: Path | None = None,
    development_policy: DevelopmentIngestionPolicy | None = None,
    fact_chunk_size: int | None = None,
    source_archive: Path | None = None,
) -> IngestionResult:
    """
    Execute the full pipeline for *fecha* (defaults to today in Argentina).

    Returns an explicit completed or skipped result. Raises on unexpected
    errors after marking the run as failed.
    """
    raw_dir = raw_dir or Path("data")
    raw_dir.mkdir(parents=True, exist_ok=True)
    retention_policy = from_environment()
    development_policy = development_policy or development_policy_from_environment()
    fact_chunk_size = fact_chunk_size_with_cli_override(chunk_size=fact_chunk_size)
    if source_archive is not None and fecha is None:
        raise ValueError("fecha is required when replaying a local source archive")
    fecha = resolve_ingestion_date(fecha)

    conn = _db_connect()
    t0 = time.perf_counter()
    run_id: int | None = None
    ingestion_lock: IngestionLock | None = None
    active_timer: StageTimer | None = None

    try:
        # ── 0. Ensure reference seeds ─────────────────────────────────────
        _ensure_seeds(conn)

        # ── 1. Download ──────────────────────────────────────────────────────
        active_timer = StageTimer(log, "download")
        active_timer.start(source="local" if source_archive is not None else "remote")
        try:
            dl = (
                downloader.register_local_archive(conn, source_archive, fecha)
                if source_archive is not None
                else downloader.download(conn, raw_dir, fecha)
            )
        except Exception as exc:
            active_timer.fail(error_type=type(exc).__name__)
            raise
        active_timer.finish(
            filename=dl.filename,
            size_bytes=dl.size_bytes,
            source="local" if source_archive is not None else "remote",
        )

        if not dl.is_new:
            if not should_process_raw_file(conn, dl.raw_file_id, partial=development_policy.enabled):
                log.info(
                    "pipeline.skipped",
                    reason="duplicate_sha256",
                    raw_file_id=dl.raw_file_id,
                    filename=dl.filename,
                )
                conn.close()
                return IngestionResult.skipped()
            log.info(
                "pipeline.reprocessing_failed_run",
                raw_file_id=dl.raw_file_id,
                filename=dl.filename,
            )

        # The lock is durable rather than connection-scoped because the
        # pipeline uses auxiliary database connections while streaming facts.
        # Recheck eligibility after acquiring it: another entrypoint may have
        # finished the same date between the first duplicate check and here.
        ingestion_lock = acquire_ingestion_lock(
            conn,
            fecha_datos=dl.fecha_datos,
            raw_file_id=dl.raw_file_id,
            owner="cli",
        )
        if not should_process_raw_file(conn, dl.raw_file_id, partial=development_policy.enabled):
            log.info(
                "pipeline.skipped",
                reason="completed_while_waiting_for_ingestion_lock",
                raw_file_id=dl.raw_file_id,
                filename=dl.filename,
            )
            return IngestionResult.skipped()

        # ── 2. Open ingestion run ────────────────────────────────────────────
        run_id = open_ingestion_run(
            conn,
            dl.raw_file_id,
            fecha_solicitada=fecha,
            fecha_logica=fecha,
            development_policy=development_policy,
            fact_chunk_size=fact_chunk_size,
        )
        logger = log.bind(run_id=run_id)
        logger.info(
            "pipeline.run.opened",
            fecha=str(dl.fecha_datos),
            is_partial=development_policy.enabled,
        )

        fecha_fuente = inspect_source_archive_date(str(dl.path))
        record_source_date(conn, run_id, fecha_fuente)
        validate_source_archive_date(
            fecha_logica=fecha,
            fecha_fuente=fecha_fuente,
        )

        # ── 3. Staging ───────────────────────────────────────────────────────
        active_timer = StageTimer(logger, "staging")
        active_timer.start()

        ingestion_lock = renew_ingestion_lock(conn, ingestion_lock)
        stg = staging_loader.load(
            conn,
            dl.path,
            run_id,
            heartbeat=lambda: renew_ingestion_lock(conn, ingestion_lock),
            development_policy=development_policy,
        )
        staging_loader.register_counts(conn, run_id, stg)
        conn.commit()

        active_timer.finish(
            inner_zips=stg.inner_zips_count,
            inner_zips_invalid=stg.inner_zips_invalid_count,
            rows_comercios=stg.rows_comercios,
            rows_sucursales=stg.rows_sucursales,
            rows_productos=stg.rows_productos,
            rows_productos_source=stg.rows_productos_source,
            rows_productos_selected=stg.rows_productos_selected,
        )
        logger.info(
            "pipeline.storage_footprint",
            stage="staging",
            **collect_storage_footprint(conn, run_id=run_id).__dict__,
        )

        # ── 4. Quality checks ────────────────────────────────────────────────
        active_timer = StageTimer(logger, "quality")
        active_timer.start()

        ingestion_lock = renew_ingestion_lock(conn, ingestion_lock)
        check_results: list[quality_checks.CheckResult] = []
        try:
            check_results = quality_checks.run_all(conn, run_id)
        except quality_checks.QualityCheckError as exc:
            reporters.report(results=exc.failures, run_id=run_id, conn=conn)
            alerting.notify_failure(
                run_id,
                message=f"Critical quality checks failed: {[r.name for r in exc.failures]}",
                fecha=dl.fecha_datos,
            )
            active_timer.fail(
                reason="critical_quality_failure",
                failures=[r.name for r in exc.failures],
            )
            logger.error("pipeline.aborted", reason="critical_quality_failure")
            raise

        qr = reporters.report(results=check_results, run_id=run_id, conn=conn)
        conn.commit()

        active_timer.finish(
            passed=qr.passed,
            warnings_failed=qr.warnings_failed,
        )

        # ── 5. Clean + load dimensions ───────────────────────────────────────
        active_timer = StageTimer(logger, "dimensions")
        active_timer.start()

        ingestion_lock = renew_ingestion_lock(conn, ingestion_lock)
        dimensions = load_staged_dimensions(
            conn, run_id, is_partial=development_policy.enabled
        )
        conn.commit()

        fecha_id_int = int(dl.fecha_datos.strftime("%Y%m%d"))

        active_timer.finish(
            rows_comercios=dimensions.rows_comercios,
            rows_sucursales=dimensions.rows_sucursales,
            rows_productos=dimensions.rows_productos,
            rows_rejected=dimensions.rows_rejected,
        )

        # ── 7. Build + load fact_precios in chunks ───────────────────────────
        active_timer = StageTimer(logger, "fact_load")
        active_timer.start(chunk_size=fact_chunk_size)

        ingestion_lock = renew_ingestion_lock(conn, ingestion_lock)
        product_load = stream_pending_product_facts(
            conn,
            _db_connect,
            run_id=run_id,
            run_date=dl.fecha_datos,
            chunk_size=fact_chunk_size,
            heartbeat=lambda: renew_ingestion_lock(conn, ingestion_lock),
        )
        fact_load_elapsed_seconds = active_timer.finish(
            chunks=product_load.chunks,
            chunk_size=fact_chunk_size,
            rows_fact=product_load.rows_fact,
            rows_valid=product_load.rows_valid,
            rows_rejected=product_load.rows_rejected,
            rows_without_keys=product_load.rows_without_keys,
            rows_deduplicated=product_load.rows_deduplicated,
        )
        from etl.fact_load_metrics import record_fact_load_metrics

        record_fact_load_metrics(
            conn,
            run_id=run_id,
            elapsed_seconds=fact_load_elapsed_seconds,
            chunks=product_load.chunks,
        )
        conn.commit()

        # ── 8. Publish facts + aggregations atomically ───────────────────────
        active_timer = StageTimer(logger, "publication")
        active_timer.start()

        ingestion_lock = renew_ingestion_lock(conn, ingestion_lock)
        reconciliation = reconciliation_for_publication(
            product_load, is_partial=development_policy.enabled
        )
        if development_policy.enabled:
            publication = complete_partial_run(
                conn, run_id=run_id, reconciliation=reconciliation
            )
        else:
            publication = publish_pending_facts(
                conn,
                run_id=run_id,
                fecha_id=fecha_id_int,
                reconciliation=reconciliation,
            )
        conn.commit()

        active_timer.finish(
            rows_fact=publication.rows_fact,
            rows_aggregated=publication.rows_aggregated,
            is_partial=development_policy.enabled,
            rows_fact_pending=reconciliation.rows_fact_pending,
            rows_fact_published=reconciliation.rows_fact_published,
            rows_valid=reconciliation.rows_valid,
            rows_rejected=reconciliation.rows_rejected,
            rows_without_keys=reconciliation.rows_without_keys,
            rows_deduplicated=reconciliation.rows_deduplicated,
        )
        logger.info(
            "pipeline.storage_footprint",
            stage="publication",
            **collect_storage_footprint(conn, run_id=run_id).__dict__,
        )

        elapsed_total_seconds = time.perf_counter() - t0
        logger.info(
            "pipeline.completed",
            fecha=str(dl.fecha_datos),
            is_partial=development_policy.enabled,
            rows_loaded=product_load.rows_fact,
            rows_fact_pending=reconciliation.rows_fact_pending,
            rows_fact_published=reconciliation.rows_fact_published,
            rows_valid=reconciliation.rows_valid,
            rows_rejected=reconciliation.rows_rejected,
            rows_without_keys=reconciliation.rows_without_keys,
            rows_deduplicated=reconciliation.rows_deduplicated,
            elapsed_total=round(elapsed_total_seconds, 3),
            peak_rss_bytes=process_max_rss_bytes(),
            fact_rows_per_second=items_per_second(
                product_load.rows_fact, elapsed_total_seconds
            ),
        )
        if development_policy.enabled:
            logger.info(
                "pipeline.partial_run.completed",
                rows_fact_pending=reconciliation.rows_fact_pending,
                rows_fact_published=0,
                retention_skipped=True,
            )
            return IngestionResult.completed(product_load.rows_fact, run_id=run_id)
        try:
            retention_result = apply_fact_retention(
                conn,
                policy=retention_policy,
                fecha_datos=dl.fecha_datos,
            )
            if retention_result is not None:
                logger.info(
                    "pipeline.fact_retention.completed",
                    cutoff=str(retention_policy.fact_cutoff(dl.fecha_datos)),
                    partitions=retention_result.partitions,
                )
        except Exception:
            # Publication is already committed. Retention is best-effort and
            # must not incorrectly turn that successful ingestion into failed.
            conn.rollback()
            logger.exception("pipeline.fact_retention.failed")
        try:
            staging_retention_result = apply_staging_retention(
                conn,
                policy=retention_policy,
                fecha_datos=dl.fecha_datos,
            )
            if staging_retention_result is not None:
                logger.info(
                    "pipeline.staging_retention.completed",
                    cutoff=str(retention_policy.staging_cutoff(dl.fecha_datos)),
                    run_ids=staging_retention_result.run_ids,
                )
        except Exception:
            # Staging cleanup is independent from fact cleanup and publication.
            conn.rollback()
            logger.exception("pipeline.staging_retention.failed")
        try:
            raw_retention_result = apply_raw_retention(
                conn,
                raw_dir=raw_dir,
                policy=retention_policy,
                fecha_datos=dl.fecha_datos,
            )
            if raw_retention_result is not None:
                logger.info(
                    "pipeline.raw_retention.completed",
                    cutoff=str(retention_policy.raw_cutoff(dl.fecha_datos)),
                    deleted_file_ids=raw_retention_result.deleted_file_ids,
                    missing_file_ids=raw_retention_result.missing_file_ids,
                )
        except Exception:
            # ZIP cleanup is last and must not affect the published snapshot.
            conn.rollback()
            logger.exception("pipeline.raw_retention.failed")
        return IngestionResult.completed(product_load.rows_fact, run_id=run_id)

    except Exception as exc:
        if active_timer is not None and active_timer.is_running:
            active_timer.fail(error_type=type(exc).__name__)
        conn.rollback()
        if run_id is not None and not isinstance(exc, quality_checks.QualityCheckError):
            reporters.mark_run_failed(conn, run_id, str(exc))
            alerting.notify_failure(run_id, str(exc), fecha=fecha)
        log.exception("pipeline.fatal_error", error=str(exc))
        raise

    finally:
        if ingestion_lock is not None:
            try:
                release_ingestion_lock(conn, ingestion_lock)
                log.info(
                    "pipeline.ingestion_lock.released",
                    fecha=str(ingestion_lock.fecha_datos),
                )
            except Exception:
                log.exception(
                    "pipeline.ingestion_lock.release_failed",
                    fecha=str(ingestion_lock.fecha_datos),
                )
        conn.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    _configure_logging()

    parser = argparse.ArgumentParser(
        description="Run the SEPA price pipeline for a given date."
    )
    parser.add_argument(
        "--fecha",
        type=date.fromisoformat,
        default=None,
        metavar="YYYY-MM-DD",
        help="Date to process (defaults to today in America/Argentina/Buenos_Aires).",
    )
    parser.add_argument(
        "--raw-dir",
        type=Path,
        default=Path("data"),
        metavar="DIR",
        help="Directory for downloaded ZIP files (default: ./data).",
    )
    parser.add_argument(
        "--zip-path",
        type=Path,
        default=None,
        metavar="ZIP",
        help="Replay an existing local ZIP; requires --fecha and skips network download.",
    )
    parser.add_argument(
        "--dev-chain-key",
        action="append",
        default=None,
        metavar="ID_COMERCIO:ID_BANDERA",
        help="Limit a local partial run to one chain; repeat for multiple chains.",
    )
    parser.add_argument(
        "--dev-max-product-rows",
        type=int,
        default=None,
        metavar="N",
        help="Limit a local partial run to N staged product rows.",
    )
    parser.add_argument(
        "--fact-chunk-size",
        type=int,
        default=None,
        metavar="N",
        help="Rows processed per product-fact batch (default: SEPA_FACT_CHUNK_SIZE or 300000).",
    )
    args = parser.parse_args()

    if args.zip_path is not None and args.fecha is None:
        parser.error("--zip-path requires --fecha so the source date can be validated")

    try:
        development_policy = with_cli_overrides(
            development_policy_from_environment(),
            chain_keys=args.dev_chain_key,
            max_product_rows=args.dev_max_product_rows,
        )
        fact_chunk_size = fact_chunk_size_with_cli_override(
            chunk_size=args.fact_chunk_size
        )
        run(
            fecha=args.fecha,
            raw_dir=args.raw_dir,
            development_policy=development_policy,
            fact_chunk_size=fact_chunk_size,
            source_archive=args.zip_path,
        )
    except Exception:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
