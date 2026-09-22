"""Shared, memory-bounded processing of staged product rows."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from typing import Callable

import pandas as pd
import structlog

from etl.load import warehouse_loader
from etl.performance import StageTimer
from etl.reconciliation import ProductReconciliation, record_product_rejections
from etl.transform.cleaner import clean_comercios, clean_productos, clean_sucursales
from etl.transform.fact_builder import (
    PRODUTO_CHUNK_SIZE,
    build_fact_chunk,
    build_sk_caches,
)

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class ProductLoadResult:
    """Counts produced while converting staged products into pending facts."""

    chunks: int
    rows_fact: int
    rows_valid: int
    rows_rejected: int
    rows_without_keys: int
    rows_deduplicated: int


@dataclass(frozen=True)
class DimensionLoadResult:
    """Counts produced while loading dimensions from a staging run."""

    rows_comercios: int
    rows_sucursales: int
    rows_productos: int
    rows_rejected: int


def reconciliation_for_publication(result: ProductLoadResult) -> ProductReconciliation:
    """Turn a completed streaming result into its publish-time reconciliation."""
    return ProductReconciliation(
        rows_valid=result.rows_valid,
        rows_rejected=result.rows_rejected,
        rows_without_keys=result.rows_without_keys,
        rows_deduplicated=result.rows_deduplicated,
        rows_fact_pending=result.rows_fact,
        rows_fact_published=result.rows_fact,
    )


def load_staged_dimensions(conn, run_id: int) -> DimensionLoadResult:
    """Clean and load every dimension needed by a staged ingestion run.

    Comercio and sucursal tables are small enough to clean in memory.  Product
    dimensions stay in PostgreSQL and are deduplicated with an INSERT…SELECT,
    avoiding a full product DataFrame in either entry point.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM stg_comercios WHERE run_id = %s", (run_id,))
        comercios_df = pd.DataFrame(
            cur.fetchall(), columns=[description[0] for description in cur.description]
        )
        cur.execute("SELECT * FROM stg_sucursales WHERE run_id = %s", (run_id,))
        sucursales_df = pd.DataFrame(
            cur.fetchall(), columns=[description[0] for description in cur.description]
        )

    comercios = clean_comercios(comercios_df)
    sucursales = clean_sucursales(sucursales_df)
    rows_comercios, rows_sucursales = warehouse_loader.load_dimensions_no_producto(
        conn, comercios.clean, sucursales.clean
    )
    rows_productos = warehouse_loader.load_dim_produto_from_staging(conn, run_id)
    return DimensionLoadResult(
        rows_comercios=rows_comercios,
        rows_sucursales=rows_sucursales,
        rows_productos=rows_productos,
        rows_rejected=len(comercios.rejected) + len(sucursales.rejected),
    )


def iter_staged_products(conn_read, run_id: int, chunk_size: int = PRODUTO_CHUNK_SIZE):
    """Yield staged product rows in bounded DataFrame chunks for one run."""
    with conn_read.cursor("stg_productos_cursor") as cur:
        cur.itersize = chunk_size
        cur.execute(
            """
            SELECT *
            FROM stg_productos
            WHERE run_id = %s
            ORDER BY id_comercio, id_sucursal, id_producto
            """,
            (run_id,),
        )
        columns: list[str] | None = None
        while rows := cur.fetchmany(chunk_size):
            if columns is None:
                columns = [description[0] for description in cur.description]
            yield pd.DataFrame(rows, columns=columns)


def stream_pending_product_facts(
    conn,
    connection_factory: Callable[[], object],
    *,
    run_id: int,
    run_date: date,
    chunk_size: int = PRODUTO_CHUNK_SIZE,
    heartbeat: Callable[[], None] | None = None,
) -> ProductLoadResult:
    """Build and load a run's pending facts without materializing all products.

    Dimensions must already be populated.  A read connection streams staging
    rows while a separate write connection commits each pending-fact chunk;
    those provisional rows cannot become visible to consumers until the caller
    publishes the run atomically. ``heartbeat`` runs once per processed chunk
    when supplied.
    """
    fecha_id, comercio_cache, sucursal_cache, producto_cache = build_sk_caches(
        conn, run_date
    )
    conn_read = connection_factory()
    conn_write = connection_factory()
    executor = ThreadPoolExecutor(max_workers=1)
    pending: Future | None = None
    chunks = 0
    rows_fact_attempted = 0
    rows_valid = 0
    rows_rejected = 0
    rows_without_keys = 0
    rows_deduplicated = 0

    try:
        for chunk_df in iter_staged_products(conn_read, run_id, chunk_size):
            if heartbeat is not None:
                heartbeat()
            chunks += 1
            timer = StageTimer(
                log.bind(run_id=run_id, chunk=chunks), "fact_chunk_preparation"
            )
            timer.start(rows_staged=len(chunk_df))
            try:
                clean_result = clean_productos(chunk_df)
                rows_valid += len(clean_result.clean)
                rows_rejected += len(clean_result.rejected)
                record_product_rejections(
                    conn,
                    run_id=run_id,
                    source="clean_productos",
                    rejected=clean_result.rejected,
                )
                fact_result = build_fact_chunk(
                    clean_result.clean,
                    comercio_cache,
                    sucursal_cache,
                    producto_cache,
                    fecha_id,
                    run_id,
                )
                rows_without_keys += len(fact_result.rejected)
                record_product_rejections(
                    conn,
                    run_id=run_id,
                    source="fact_key_resolution",
                    rejected=fact_result.rejected,
                )
                chunk_rows_deduplicated = (
                    len(clean_result.clean)
                    - len(fact_result.rejected)
                    - len(fact_result.df)
                )
                rows_deduplicated += chunk_rows_deduplicated
                rows_fact_attempted += len(fact_result.df)

                timer.finish(
                    rows_valid=len(clean_result.clean),
                    rows_rejected=len(clean_result.rejected),
                    rows_without_keys=len(fact_result.rejected),
                    rows_deduplicated=chunk_rows_deduplicated,
                    rows_fact_pending=len(fact_result.df),
                )
            except Exception:
                timer.fail()
                raise

            if pending is not None:
                pending.result()

            pending = executor.submit(
                _commit_pending_fact_chunk, conn_write, fact_result.df, run_id, chunks
            )

        if pending is not None:
            pending.result()
    finally:
        executor.shutdown(wait=True)
        conn_write.close()
        conn_read.close()

    rows_fact = _pending_fact_count(conn, run_id)
    rows_deduplicated += rows_fact_attempted - rows_fact
    warehouse_loader.register_rows_loaded(conn, run_id, rows_fact)
    return ProductLoadResult(
        chunks=chunks,
        rows_fact=rows_fact,
        rows_valid=rows_valid,
        rows_rejected=rows_rejected,
        rows_without_keys=rows_without_keys,
        rows_deduplicated=rows_deduplicated,
    )


def _commit_pending_fact_chunk(
    conn, df: pd.DataFrame, run_id: int, chunk: int
) -> int:
    """Persist one chunk and emit its isolated database-write measurement."""
    timer = StageTimer(log.bind(run_id=run_id, chunk=chunk), "pending_fact_write")
    timer.start(rows_fact_pending=len(df))
    try:
        rows = warehouse_loader.load_pending_fact(conn, df, run_id)
        conn.commit()
    except Exception:
        timer.fail()
        raise
    timer.finish(rows_persisted=rows)
    return rows


def _pending_fact_count(conn, run_id: int) -> int:
    """Return the unique provisional facts retained for a run."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM pending_fact_precios WHERE run_id = %s", (run_id,)
        )
        return cur.fetchone()[0]
