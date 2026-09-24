"""Atomically publish the pending facts of a completed ingestion run."""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter

import structlog

from etl.load.aggregator import aggregate
from etl.load.warehouse_loader import _ensure_partition
from etl.reconciliation import ProductReconciliation, record_product_reconciliation
from etl.publication_metrics import record_publication_metrics

log = structlog.get_logger(__name__)


@dataclass
class PublicationResult:
    run_id: int
    fecha_id: int
    rows_fact: int
    rows_aggregated: int


def complete_partial_run(
    conn,
    *,
    run_id: int,
    reconciliation: ProductReconciliation,
) -> PublicationResult:
    """Complete a bounded run without exposing its pending facts.

    Pending facts stay scoped to the run for local inspection. No visible facts
    or aggregates are changed, and the reconciliation records zero published
    facts so the audit trail cannot present this as a full snapshot.
    """
    if not reconciliation.is_partial:
        raise ValueError("Partial completion requires a partial reconciliation")
    with conn.cursor() as cur:
        cur.execute(
            "SELECT is_partial FROM ingestion_runs WHERE id = %s",
            (run_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"Cannot complete unknown ingestion run {run_id}")
        if not row[0]:
            raise ValueError(f"Cannot complete full ingestion run {run_id} as partial")
        cur.execute(
            "SELECT COUNT(*) FROM pending_fact_precios WHERE run_id = %s",
            (run_id,),
        )
        if cur.fetchone()[0] != reconciliation.rows_fact_pending:
            raise ValueError(f"Partial run {run_id} pending facts do not reconcile")

    record_product_reconciliation(conn, run_id, reconciliation)
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE ingestion_runs
               SET status = 'completed', finished_at = clock_timestamp(),
                   rows_valid = %s, error_message = NULL
             WHERE id = %s AND is_partial = TRUE
            """,
            (reconciliation.rows_valid, run_id),
        )
        if cur.rowcount != 1:
            raise ValueError(f"Cannot complete full ingestion run {run_id} as partial")
    result = PublicationResult(
        run_id=run_id, fecha_id=0, rows_fact=0, rows_aggregated=0
    )
    log.info("publisher.partial_run_completed", **result.__dict__)
    return result


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
        cur.execute("SELECT is_partial FROM ingestion_runs WHERE id = %s", (run_id,))
        row = cur.fetchone()
    if row is None:
        raise ValueError(f"Cannot publish unknown ingestion run {run_id}")
    if row[0]:
        raise ValueError(f"Cannot publish partial ingestion run {run_id}")

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

    delete_existing_seconds, insert_facts_seconds, rows_fact = _swap_daily_partition(
        conn, run_id=run_id, fecha_id=fecha_id
    )

    if rows_fact != reconciliation.rows_fact_published:
        raise ValueError(
            f"Cannot publish run {run_id}: expected {reconciliation.rows_fact_published} "
            f"published facts, inserted {rows_fact}"
        )

    agg = aggregate(conn, fecha_id)

    record_product_reconciliation(conn, run_id, reconciliation)

    with conn.cursor() as cur:
        started_at = perf_counter()
        cur.execute(
            """
            UPDATE ingestion_runs
               SET status = 'completed',
                   finished_at = clock_timestamp(),
                   rows_valid = %s,
                   error_message = NULL
             WHERE id = %s
            """,
            (reconciliation.rows_valid, run_id),
        )
        if cur.rowcount != 1:
            raise ValueError(f"Cannot publish unknown ingestion run {run_id}")
        cur.execute("DELETE FROM pending_fact_precios WHERE run_id = %s", (run_id,))
        cleanup_seconds = perf_counter() - started_at

    record_publication_metrics(
        conn,
        run_id=run_id,
        delete_existing_seconds=delete_existing_seconds,
        insert_facts_seconds=insert_facts_seconds,
        aggregate_delete_seconds=agg.delete_seconds,
        aggregate_insert_seconds=agg.insert_seconds,
        cleanup_seconds=cleanup_seconds,
    )

    result = PublicationResult(
        run_id=run_id,
        fecha_id=fecha_id,
        rows_fact=rows_fact,
        rows_aggregated=agg.rows_upserted,
    )
    log.info(
        "publisher.pending_facts_published",
        **result.__dict__,
        delete_existing_seconds=round(delete_existing_seconds, 3),
        insert_facts_seconds=round(insert_facts_seconds, 3),
        aggregate_delete_seconds=round(agg.delete_seconds, 3),
        aggregate_insert_seconds=round(agg.insert_seconds, 3),
        cleanup_seconds=round(cleanup_seconds, 3),
    )
    return result


def _swap_daily_partition(conn, *, run_id: int, fecha_id: int) -> tuple[float, float, int]:
    """Build a detached daily leaf, then replace the visible leaf atomically."""
    daily_partition = _ensure_partition(conn, fecha_id)
    monthly_partition = daily_partition[:19]
    candidate = f"{daily_partition}_run_{run_id}"
    with conn.cursor() as cur:
        started_at = perf_counter()
        cur.execute(f"CREATE TABLE {candidate} (LIKE {daily_partition} INCLUDING ALL)")
        cur.execute(
            f"ALTER TABLE {candidate} ADD CONSTRAINT {candidate}_fecha_check "
            f"CHECK (fecha_id = {fecha_id})"
        )
        cur.execute(
            f"""
            INSERT INTO {candidate} (
                producto_sk, sucursal_sk, fecha_id, run_id,
                precio_lista, precio_referencia, precio_promo1, precio_promo2,
                leyenda_promo1, leyenda_promo2, tiene_promo
            )
            SELECT
                producto_sk, sucursal_sk, fecha_id, run_id,
                precio_lista, precio_referencia, precio_promo1, precio_promo2,
                leyenda_promo1, leyenda_promo2, tiene_promo
            FROM pending_fact_precios
            WHERE run_id = %s AND fecha_id = %s
            """,
            (run_id, fecha_id),
        )
        rows_fact = cur.rowcount
        insert_facts_seconds = perf_counter() - started_at

        started_at = perf_counter()
        cur.execute(f"ALTER TABLE {monthly_partition} DETACH PARTITION {daily_partition}")
        cur.execute(
            f"ALTER TABLE {monthly_partition} ATTACH PARTITION {candidate} "
            f"FOR VALUES FROM ({fecha_id}) TO ({fecha_id + 1})"
        )
        cur.execute(f"DROP TABLE {daily_partition}")
        delete_existing_seconds = perf_counter() - started_at
    return delete_existing_seconds, insert_facts_seconds, rows_fact
