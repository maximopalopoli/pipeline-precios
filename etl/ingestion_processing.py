"""Shared, memory-bounded processing of staged product rows."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from typing import Callable

import pandas as pd

from etl.load import warehouse_loader
from etl.transform.cleaner import clean_comercios, clean_productos, clean_sucursales
from etl.transform.fact_builder import (
    PRODUTO_CHUNK_SIZE,
    build_fact_chunk,
    build_sk_caches,
)


@dataclass(frozen=True)
class ProductLoadResult:
    """Counts produced while converting staged products into pending facts."""

    chunks: int
    rows_fact: int
    rows_valid: int


@dataclass(frozen=True)
class DimensionLoadResult:
    """Counts produced while loading dimensions from a staging run."""

    rows_comercios: int
    rows_sucursales: int
    rows_productos: int
    rows_rejected: int


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
    rows_fact = 0
    rows_valid = 0

    try:
        for chunk_df in iter_staged_products(conn_read, run_id, chunk_size):
            if heartbeat is not None:
                heartbeat()
            chunks += 1
            clean_result = clean_productos(chunk_df)
            rows_valid += len(clean_result.clean)
            fact_result = build_fact_chunk(
                clean_result.clean,
                comercio_cache,
                sucursal_cache,
                producto_cache,
                fecha_id,
                run_id,
            )

            if pending is not None:
                rows_fact += pending.result()

            pending = executor.submit(
                _commit_pending_fact_chunk, conn_write, fact_result.df, run_id
            )

        if pending is not None:
            rows_fact += pending.result()
    finally:
        executor.shutdown(wait=True)
        conn_write.close()
        conn_read.close()

    warehouse_loader.register_rows_loaded(conn, run_id, rows_fact)
    return ProductLoadResult(chunks=chunks, rows_fact=rows_fact, rows_valid=rows_valid)


def _commit_pending_fact_chunk(conn, df: pd.DataFrame, run_id: int) -> int:
    rows = warehouse_loader.load_pending_fact(conn, df, run_id)
    conn.commit()
    return rows
