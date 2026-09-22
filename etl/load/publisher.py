"""Atomically publish the pending facts of a completed ingestion run."""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from etl.load.aggregator import aggregate
from etl.load.warehouse_loader import _ensure_partition
from etl.reconciliation import ProductReconciliation, record_product_reconciliation

log = structlog.get_logger(__name__)


@dataclass
class PublicationResult:
    run_id: int
    fecha_id: int
    rows_fact: int
    rows_aggregated: int


def publish_pending_facts(
    conn,
    *,
    run_id: int,
    fecha_id: int,
    reconciliation: ProductReconciliation,
) -> PublicationResult:
    """Replace one day's visible facts and aggregates from a pending run.

    The caller owns the transaction.  It must commit this function as one unit
    or roll it back if any statement fails.  Pending rows are only deleted once
    they have been copied into ``fact_precios`` and their aggregates rebuilt.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                COUNT(*) AS total_rows,
                COUNT(*) FILTER (WHERE fecha_id = %s) AS rows_for_fecha
            FROM pending_fact_precios
            WHERE run_id = %s
            """,
            (fecha_id, run_id),
        )
        total_rows, rows_for_fecha = cur.fetchone()

    if total_rows == 0:
        raise ValueError(f"Cannot publish run {run_id}: no pending facts")
    if total_rows != rows_for_fecha:
        raise ValueError(
            f"Cannot publish run {run_id}: pending facts do not match fecha_id {fecha_id}"
        )
    if total_rows != reconciliation.rows_fact_pending:
        raise ValueError(
            f"Cannot publish run {run_id}: expected {reconciliation.rows_fact_pending} "
            f"pending facts, found {total_rows}"
        )

    _ensure_partition(conn, fecha_id)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM fact_precios WHERE fecha_id = %s", (fecha_id,))
        cur.execute(
            """
            INSERT INTO fact_precios (
                producto_sk, sucursal_sk, fecha_id, run_id,
                precio_lista, precio_referencia,
                precio_promo1, precio_promo2,
                leyenda_promo1, leyenda_promo2,
                tiene_promo
            )
            SELECT
                producto_sk, sucursal_sk, fecha_id, run_id,
                precio_lista, precio_referencia,
                precio_promo1, precio_promo2,
                leyenda_promo1, leyenda_promo2,
                tiene_promo
            FROM pending_fact_precios
            WHERE run_id = %s AND fecha_id = %s
            """,
            (run_id, fecha_id),
        )
        rows_fact = cur.rowcount

    if rows_fact != reconciliation.rows_fact_published:
        raise ValueError(
            f"Cannot publish run {run_id}: expected {reconciliation.rows_fact_published} "
            f"published facts, inserted {rows_fact}"
        )

    agg = aggregate(conn, fecha_id)

    record_product_reconciliation(conn, run_id, reconciliation)

    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE ingestion_runs
               SET status = 'completed',
                   finished_at = NOW(),
                   rows_valid = %s,
                   error_message = NULL
             WHERE id = %s
            """,
            (reconciliation.rows_valid, run_id),
        )
        if cur.rowcount != 1:
            raise ValueError(f"Cannot publish unknown ingestion run {run_id}")
        cur.execute("DELETE FROM pending_fact_precios WHERE run_id = %s", (run_id,))

    result = PublicationResult(
        run_id=run_id,
        fecha_id=fecha_id,
        rows_fact=rows_fact,
        rows_aggregated=agg.rows_upserted,
    )
    log.info("publisher.pending_facts_published", **result.__dict__)
    return result
