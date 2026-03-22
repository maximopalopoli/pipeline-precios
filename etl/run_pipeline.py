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
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import date
from pathlib import Path

import pandas as pd
import psycopg2
import structlog
import structlog.dev

from etl import alerting
from etl.extract import downloader
from etl.load.aggregator import aggregate
from etl.load import warehouse_loader
from etl.load.warehouse_loader import load as warehouse_load
from etl.quality import checks as quality_checks
from etl.quality import reporters
from etl.staging import loader as staging_loader
from etl.transform.cleaner import clean_comercios, clean_productos, clean_sucursales
from etl.transform.fact_builder import (
    PRODUTO_CHUNK_SIZE,
    build_fact_chunk,
    build_sk_caches,
)

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
# Run lifecycle helpers
# ---------------------------------------------------------------------------


def _has_completed_run(conn, raw_file_id: int) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM ingestion_runs WHERE raw_file_id = %s AND status = 'completed'",
            (raw_file_id,),
        )
        return cur.fetchone() is not None


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


def _read_small_staging(
    conn, run_id: int
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (comercios_df, sucursales_df) — the small tables only."""
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM stg_comercios  WHERE run_id = %s", (run_id,))
        comercios = pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])

        cur.execute("SELECT * FROM stg_sucursales WHERE run_id = %s", (run_id,))
        sucursales = pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])

    return comercios, sucursales


def _iter_stg_productos(conn_read, run_id: int, chunk_size: int):
    """Yield stg_productos rows for *run_id* as DataFrames of at most *chunk_size* rows.

    Uses a server-side cursor on a separate read connection so the caller can
    commit on its write connection without closing the cursor.
    """
    with conn_read.cursor("stg_productos_cursor") as cur:
        cur.itersize = chunk_size
        cur.execute(
            "SELECT * FROM stg_productos WHERE run_id = %s ORDER BY id_comercio, id_sucursal, id_producto",
            (run_id,),
        )
        cols: list[str] | None = None
        while True:
            rows = cur.fetchmany(chunk_size)
            if not rows:
                break
            if cols is None:
                cols = [d[0] for d in cur.description]
            yield pd.DataFrame(rows, columns=cols)


# ---------------------------------------------------------------------------
# Double-buffer helper
# ---------------------------------------------------------------------------


def _commit_fact_chunk(conn_write, df: "pd.DataFrame", run_id: int) -> int:
    """Load a single fact chunk and commit (runs in background thread)."""
    rows = warehouse_loader.load_fact(conn_write, df, run_id)
    conn_write.commit()
    return rows


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
            if _has_completed_run(conn, dl.raw_file_id):
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

        # ── 5. Clean small staging tables (comercios + sucursales) ───────────
        logger.info("pipeline.stage.start", stage="clean")
        t4 = time.perf_counter()

        stg_comercios, stg_sucursales = _read_small_staging(conn, run_id)
        clean_c = clean_comercios(stg_comercios)
        clean_s = clean_sucursales(stg_sucursales)

        logger.info(
            "pipeline.stage.done",
            stage="clean",
            comercios=len(clean_c.clean),
            sucursales=len(clean_s.clean),
            rejected=len(clean_c.rejected) + len(clean_s.rejected),
            elapsed=round(time.perf_counter() - t4, 2),
        )

        # ── 6. Load dimensions ───────────────────────────────────────────────
        logger.info("pipeline.stage.start", stage="dimensions")
        t5 = time.perf_counter()

        # comercio + sucursal via Python (small tables)
        rc, rs = warehouse_loader.load_dimensions_no_producto(
            conn, clean_c.clean, clean_s.clean
        )
        # producto via SQL INSERT...SELECT from staging (avoids 14M rows in Python)
        rp = warehouse_loader.load_dim_produto_from_staging(conn, run_id)
        conn.commit()

        # Build SK lookup caches from the now-populated dimension tables
        fecha_id_int, comercio_cache, sucursal_cache, producto_cache = build_sk_caches(
            conn, dl.fecha_datos
        )

        logger.info(
            "pipeline.stage.done",
            stage="dimensions",
            rows_comercios=rc,
            rows_sucursales=rs,
            rows_productos=rp,
            elapsed=round(time.perf_counter() - t5, 2),
        )

        # ── 7. Build + load fact_precios in chunks ───────────────────────────
        logger.info("pipeline.stage.start", stage="fact_load")
        t6 = time.perf_counter()

        total_fact_rows = 0
        total_productos_valid = 0
        chunk_num = 0

        # DELETE existing rows for this fecha — makes ON CONFLICT nearly free
        warehouse_loader.delete_fact_for_fecha(conn, fecha_id_int)
        conn.commit()

        # Dedicated write connection for the background thread (psycopg2 is not thread-safe)
        conn_write = _db_connect()
        conn_read = _db_connect()
        executor = ThreadPoolExecutor(max_workers=1)
        pending: Future | None = None

        try:
            for chunk_df in _iter_stg_productos(conn_read, run_id, PRODUTO_CHUNK_SIZE):
                chunk_num += 1
                clean_p = clean_productos(chunk_df)
                total_productos_valid += len(clean_p.clean)

                chunk_result = build_fact_chunk(
                    clean_p.clean,
                    comercio_cache,
                    sucursal_cache,
                    producto_cache,
                    fecha_id_int,
                    run_id,
                )

                # Wait for previous chunk's write before submitting next
                if pending is not None:
                    total_fact_rows += pending.result()

                # Submit this chunk's DB write to background thread
                pending = executor.submit(
                    _commit_fact_chunk, conn_write, chunk_result.df, run_id
                )
                del chunk_df, clean_p, chunk_result

            # Wait for the last chunk
            if pending is not None:
                total_fact_rows += pending.result()
        finally:
            executor.shutdown(wait=False)
            conn_write.close()
            conn_read.close()

        warehouse_loader.register_rows_loaded(conn, run_id, total_fact_rows)
        conn.commit()

        logger.info(
            "pipeline.stage.done",
            stage="fact_load",
            chunks=chunk_num,
            rows_fact=total_fact_rows,
            elapsed=round(time.perf_counter() - t6, 2),
        )

        # ── 8. Aggregations ──────────────────────────────────────────────────
        logger.info("pipeline.stage.start", stage="aggregations")
        t7 = time.perf_counter()

        agg = aggregate(conn, fecha_id_int)
        conn.commit()

        logger.info(
            "pipeline.stage.done",
            stage="aggregations",
            rows_upserted=agg.rows_upserted,
            elapsed=round(time.perf_counter() - t7, 2),
        )

        # ── 9. Mark completed ────────────────────────────────────────────────
        _mark_completed(conn, run_id, rows_valid=total_productos_valid)

        elapsed_total = round(time.perf_counter() - t0, 2)
        logger.info(
            "pipeline.completed",
            fecha=str(dl.fecha_datos),
            rows_loaded=total_fact_rows,
            elapsed_total=elapsed_total,
        )
        return total_fact_rows

    except Exception as exc:
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
