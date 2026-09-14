"""
run_pipeline.py — End-to-end SEPA price pipeline.

Execution order
---------------
0.  Ensure reference seeds (dim_provincia, dim_fecha) are loaded
1.  Download daily SEPA ZIP → register in raw_files (skip if SHA-256 already seen)
2.  Open ingestion_run (status = 'running')
3.  Load staging  (stg_comercios / stg_sucursales / stg_productos)
4.  Quality checks (CRITICAL failures abort the run)
5.  Clean / type-cast staging rows
6.  Resolve surrogate keys + build fact_precios DataFrame
7.  Bulk-load warehouse (dimensions + fact)
8.  Mark run 'completed'

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
from etl.extract import downloader
from etl.ingestion_run import open_ingestion_run, should_process_raw_file
from etl.ingestion_processing import load_staged_dimensions, stream_pending_product_facts
from etl.load.publisher import publish_pending_facts
from etl.quality import checks as quality_checks
from etl.quality import reporters
from etl.staging import loader as staging_loader

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
) -> int:
    """
    Execute the full pipeline for *fecha* (defaults to today).

    Returns the number of fact rows loaded, or 0 if the file was already
    processed (duplicate SHA-256) or a quality check aborted the run.
    Raises on unexpected errors after marking the run as failed.
    """
    raw_dir = raw_dir or Path("data")
    raw_dir.mkdir(parents=True, exist_ok=True)

    conn = _db_connect()
    t0 = time.perf_counter()
    run_id: int | None = None

    try:
        # ── 0. Ensure reference seeds ─────────────────────────────────────
        _ensure_seeds(conn)

        # ── 1. Download ──────────────────────────────────────────────────────
        log.info("pipeline.stage.start", stage="download")
        t1 = time.perf_counter()

        dl = downloader.download(conn, raw_dir, fecha)

        if not dl.is_new:
            if not should_process_raw_file(conn, dl.raw_file_id):
                log.info(
                    "pipeline.skipped",
                    reason="duplicate_sha256",
                    raw_file_id=dl.raw_file_id,
                    filename=dl.filename,
                )
                conn.close()
                return 0
            log.info(
                "pipeline.reprocessing_failed_run",
                raw_file_id=dl.raw_file_id,
                filename=dl.filename,
            )

        log.info(
            "pipeline.stage.done",
            stage="download",
            filename=dl.filename,
            size_bytes=dl.size_bytes,
            elapsed=round(time.perf_counter() - t1, 2),
        )

        # ── 2. Open ingestion run ────────────────────────────────────────────
        run_id = open_ingestion_run(conn, dl.raw_file_id)
        logger = log.bind(run_id=run_id)
        logger.info("pipeline.run.opened", fecha=str(dl.fecha_datos))

        # ── 3. Staging ───────────────────────────────────────────────────────
        logger.info("pipeline.stage.start", stage="staging")
        t2 = time.perf_counter()

        stg = staging_loader.load(conn, dl.path, run_id)
        staging_loader.register_counts(conn, run_id, stg)
        conn.commit()

        logger.info(
            "pipeline.stage.done",
            stage="staging",
            inner_zips=stg.inner_zips_count,
            rows_comercios=stg.rows_comercios,
            rows_sucursales=stg.rows_sucursales,
            rows_productos=stg.rows_productos,
            elapsed=round(time.perf_counter() - t2, 2),
        )

        # ── 4. Quality checks ────────────────────────────────────────────────
        logger.info("pipeline.stage.start", stage="quality")
        t3 = time.perf_counter()

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
            logger.error(
                "pipeline.aborted",
                reason="critical_quality_failure",
                failures=[r.name for r in exc.failures],
                elapsed=round(time.perf_counter() - t3, 2),
            )
            return 0

        qr = reporters.report(results=check_results, run_id=run_id, conn=conn)
        conn.commit()

        logger.info(
            "pipeline.stage.done",
            stage="quality",
            passed=qr.passed,
            warnings_failed=qr.warnings_failed,
            elapsed=round(time.perf_counter() - t3, 2),
        )

        # ── 5. Clean + load dimensions ───────────────────────────────────────
        logger.info("pipeline.stage.start", stage="dimensions")
        t5 = time.perf_counter()

        dimensions = load_staged_dimensions(conn, run_id)
        conn.commit()

        fecha_id_int = int(dl.fecha_datos.strftime("%Y%m%d"))

        logger.info(
            "pipeline.stage.done",
            stage="dimensions",
            rows_comercios=dimensions.rows_comercios,
            rows_sucursales=dimensions.rows_sucursales,
            rows_productos=dimensions.rows_productos,
            rows_rejected=dimensions.rows_rejected,
            elapsed=round(time.perf_counter() - t5, 2),
        )

        # ── 7. Build + load fact_precios in chunks ───────────────────────────
        logger.info("pipeline.stage.start", stage="fact_load")
        t6 = time.perf_counter()

        product_load = stream_pending_product_facts(
            conn,
            _db_connect,
            run_id=run_id,
            run_date=dl.fecha_datos,
        )
        conn.commit()

        logger.info(
            "pipeline.stage.done",
            stage="fact_load",
            chunks=product_load.chunks,
            rows_fact=product_load.rows_fact,
            elapsed=round(time.perf_counter() - t6, 2),
        )

        # ── 8. Publish facts + aggregations atomically ───────────────────────
        logger.info("pipeline.stage.start", stage="publication")
        t7 = time.perf_counter()

        publication = publish_pending_facts(
            conn,
            run_id=run_id,
            fecha_id=fecha_id_int,
            rows_valid=product_load.rows_valid,
        )
        conn.commit()

        logger.info(
            "pipeline.stage.done",
            stage="publication",
            rows_fact=publication.rows_fact,
            rows_aggregated=publication.rows_aggregated,
            elapsed=round(time.perf_counter() - t7, 2),
        )

        elapsed_total = round(time.perf_counter() - t0, 2)
        logger.info(
            "pipeline.completed",
            fecha=str(dl.fecha_datos),
            rows_loaded=product_load.rows_fact,
            elapsed_total=elapsed_total,
        )
        return product_load.rows_fact

    except Exception as exc:
        conn.rollback()
        if run_id is not None:
            reporters.mark_run_failed(conn, run_id, str(exc))
            alerting.notify_failure(run_id, str(exc), fecha=fecha)
        log.exception("pipeline.fatal_error", error=str(exc))
        raise

    finally:
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
        help="Date to process (defaults to today).",
    )
    parser.add_argument(
        "--raw-dir",
        type=Path,
        default=Path("data"),
        metavar="DIR",
        help="Directory for downloaded ZIP files (default: ./data).",
    )
    args = parser.parse_args()

    rows = run(fecha=args.fecha, raw_dir=args.raw_dir)
    sys.exit(0 if rows >= 0 else 1)


if __name__ == "__main__":
    main()
