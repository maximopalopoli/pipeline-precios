"""
run_pipeline.py — End-to-end SEPA price pipeline.

Execution order
---------------
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

import pandas as pd
import psycopg2
import structlog
import structlog.dev

from etl.extract import downloader
from etl.load.aggregator import aggregate
from etl.load.warehouse_loader import load as warehouse_load
from etl.quality import checks as quality_checks
from etl.quality import reporters
from etl.staging import loader as staging_loader
from etl.transform.cleaner import clean_comercios, clean_productos, clean_sucursales
from etl.transform.fact_builder import build_fact_df

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
# Run lifecycle helpers
# ---------------------------------------------------------------------------


def _open_run(conn, raw_file_id: int) -> int:
    """Insert an ingestion_run in 'running' status; return its id."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ingestion_runs (raw_file_id, status) VALUES (%s, 'running') RETURNING id",
            (raw_file_id,),
        )
        run_id: int = cur.fetchone()[0]
    conn.commit()
    return run_id


def _mark_completed(conn, run_id: int, rows_valid: int) -> None:
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


# ---------------------------------------------------------------------------
# Staging read-back
# ---------------------------------------------------------------------------


def _read_staging(
    conn, run_id: int
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Return (comercios_df, sucursales_df, productos_df) from stg_* tables."""
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM stg_comercios  WHERE run_id = %s", (run_id,))
        comercios = pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])

        cur.execute("SELECT * FROM stg_sucursales WHERE run_id = %s", (run_id,))
        sucursales = pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])

        cur.execute("SELECT * FROM stg_productos  WHERE run_id = %s", (run_id,))
        productos = pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])

    return comercios, sucursales, productos


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
        # ── 1. Download ──────────────────────────────────────────────────────
        log.info("pipeline.stage.start", stage="download")
        t1 = time.perf_counter()

        dl = downloader.download(conn, raw_dir, fecha)

        if not dl.is_new:
            log.info(
                "pipeline.skipped",
                reason="duplicate_sha256",
                raw_file_id=dl.raw_file_id,
                filename=dl.filename,
            )
            conn.close()
            return 0

        log.info(
            "pipeline.stage.done",
            stage="download",
            filename=dl.filename,
            size_bytes=dl.size_bytes,
            elapsed=round(time.perf_counter() - t1, 2),
        )

        # ── 2. Open ingestion run ────────────────────────────────────────────
        run_id = _open_run(conn, dl.raw_file_id)
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

        # ── 5. Clean ─────────────────────────────────────────────────────────
        logger.info("pipeline.stage.start", stage="clean")
        t4 = time.perf_counter()

        stg_comercios, stg_sucursales, stg_productos = _read_staging(conn, run_id)

        clean_c = clean_comercios(stg_comercios)
        clean_s = clean_sucursales(stg_sucursales)
        clean_p = clean_productos(stg_productos)

        total_rejected = (
            len(clean_c.rejected) + len(clean_s.rejected) + len(clean_p.rejected)
        )
        logger.info(
            "pipeline.stage.done",
            stage="clean",
            comercios=len(clean_c.clean),
            sucursales=len(clean_s.clean),
            productos=len(clean_p.clean),
            rejected=total_rejected,
            elapsed=round(time.perf_counter() - t4, 2),
        )

        # ── 6. Build fact DataFrame ──────────────────────────────────────────
        logger.info("pipeline.stage.start", stage="fact_build")
        t5 = time.perf_counter()

        fact_result = build_fact_df(
            conn,
            productos=clean_p.clean,
            sucursales=clean_s.clean,
            comercios=clean_c.clean,
            run_date=dl.fecha_datos,
            run_id=run_id,
        )

        logger.info(
            "pipeline.stage.done",
            stage="fact_build",
            fact_rows=len(fact_result.df),
            rejected=len(fact_result.rejected),
            elapsed=round(time.perf_counter() - t5, 2),
        )

        # ── 7. Warehouse load ────────────────────────────────────────────────
        logger.info("pipeline.stage.start", stage="warehouse")
        t6 = time.perf_counter()

        wh = warehouse_load(
            conn,
            comercios=clean_c.clean,
            sucursales=clean_s.clean,
            productos=clean_p.clean,
            fact_df=fact_result.df,
            run_id=run_id,
        )
        conn.commit()

        logger.info(
            "pipeline.stage.done",
            stage="warehouse",
            rows_comercios=wh.rows_comercios,
            rows_sucursales=wh.rows_sucursales,
            rows_productos=wh.rows_productos,
            rows_fact=wh.rows_fact,
            elapsed=round(time.perf_counter() - t6, 2),
        )

        # ── 8. Aggregations ──────────────────────────────────────────────────
        logger.info("pipeline.stage.start", stage="aggregations")
        t7 = time.perf_counter()

        fecha_id = int(dl.fecha_datos.strftime("%Y%m%d"))
        agg = aggregate(conn, fecha_id)
        conn.commit()

        logger.info(
            "pipeline.stage.done",
            stage="aggregations",
            rows_upserted=agg.rows_upserted,
            elapsed=round(time.perf_counter() - t7, 2),
        )

        # ── 9. Mark completed ────────────────────────────────────────────────
        _mark_completed(conn, run_id, rows_valid=len(clean_p.clean))

        elapsed_total = round(time.perf_counter() - t0, 2)
        logger.info(
            "pipeline.completed",
            fecha=str(dl.fecha_datos),
            rows_loaded=wh.rows_fact,
            elapsed_total=elapsed_total,
        )
        return wh.rows_fact

    except Exception as exc:
        if run_id is not None:
            reporters.mark_run_failed(conn, run_id, str(exc))
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
