"""Atomically publish the pending facts of a completed ingestion run."""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter

import structlog

from etl.load.warehouse_loader import _ensure_partition
from etl.reconciliation import ProductReconciliation, record_product_reconciliation
from etl.publication_metrics import record_publication_metrics

log = structlog.get_logger(__name__)


@dataclass
class PublicationResult:
    run_id: int
    fecha_id: int
    rows_fact: int
    rows_carried_forward: int = 0


def complete_partial_run(
    conn,
    *,
    run_id: int,
    reconciliation: ProductReconciliation,
) -> PublicationResult:
    """Complete a bounded run without exposing its pending facts.

    Pending facts stay scoped to the run for local inspection until a later
    partial run completes and another run opens. No visible facts
    are changed, and the reconciliation records zero published
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
        run_id=run_id, fecha_id=0, rows_fact=0
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
    """Replace one day's visible facts from a pending run.

    The caller owns the transaction.  It must commit this function as one unit
    or roll it back if any statement fails.  Pending rows are only deleted once
    they have been copied into ``fact_precios``.
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

    source_fecha_id = _find_carry_source(conn, run_id=run_id, fecha_id=fecha_id)
    partition_swap_seconds, insert_facts_seconds, rows_source, rows_carried = _swap_daily_partition(
        conn, run_id=run_id, fecha_id=fecha_id, source_fecha_id=source_fecha_id
    )

    if rows_source != reconciliation.rows_fact_published:
        raise ValueError(
            f"Cannot publish run {run_id}: expected {reconciliation.rows_fact_published} "
            f"source facts, inserted {rows_source}"
        )

    rows_fact = rows_source + rows_carried
    promo_legends_registered = _register_promo_legends(conn, fecha_id=fecha_id)

    record_product_reconciliation(conn, run_id, reconciliation)

    with conn.cursor() as cur:
        started_at = perf_counter()
        cur.execute(
            """
            UPDATE ingestion_runs
               SET status = 'completed',
                   finished_at = clock_timestamp(),
                   rows_valid = %s,
                   rows_fact_carried_forward = %s,
                   rows_fact_snapshot = %s,
                   error_message = NULL
             WHERE id = %s
            """,
            (reconciliation.rows_valid, rows_carried, rows_fact, run_id),
        )
        if cur.rowcount != 1:
            raise ValueError(f"Cannot publish unknown ingestion run {run_id}")
        cur.execute("DELETE FROM pending_fact_precios WHERE run_id = %s", (run_id,))
        cleanup_seconds = perf_counter() - started_at

    record_publication_metrics(
        conn,
        run_id=run_id,
        delete_existing_seconds=0,
        insert_facts_seconds=insert_facts_seconds,
        cleanup_seconds=cleanup_seconds,
        partition_swap_seconds=partition_swap_seconds,
    )

    result = PublicationResult(
        run_id=run_id,
        fecha_id=fecha_id,
        rows_fact=rows_fact,
        rows_carried_forward=rows_carried,
    )
    log.info(
        "publisher.pending_facts_published",
        **result.__dict__,
        promo_legends_registered=promo_legends_registered,
        partition_swap_seconds=round(partition_swap_seconds, 3),
        insert_facts_seconds=round(insert_facts_seconds, 3),
        cleanup_seconds=round(cleanup_seconds, 3),
    )
    return result


def _register_promo_legends(conn, *, fecha_id: int) -> int:
    """Store the reading of each new promotion legend of the published day.

    The API reads a promotion's requirement and validity from
    ``promo_leyendas``; a day has few distinct legends, so reading each once
    here keeps those regular expressions out of every price query.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO promo_leyendas (leyenda, condicion, vigencia_desde, vigencia_hasta)
            SELECT
                leyenda,
                pipeline_promo_condition(leyenda),
                pipeline_promo_valid_from(leyenda),
                pipeline_promo_valid_until(leyenda)
            FROM (
                SELECT leyenda_promo1 FROM fact_precios
                WHERE fecha_id = %s AND tiene_promo
                UNION
                SELECT leyenda_promo2 FROM fact_precios
                WHERE fecha_id = %s AND tiene_promo
            ) AS legends(leyenda)
            WHERE leyenda IS NOT NULL
            ON CONFLICT (leyenda) DO NOTHING
            """,
            (fecha_id, fecha_id),
        )
        return cur.rowcount


def _find_carry_source(conn, *, run_id: int, fecha_id: int) -> int | None:
    """Find the newest available snapshot at or before this date in the same scope."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT TO_CHAR(rf.fecha_datos, 'YYYYMMDD')::int
            FROM ingestion_runs previous
            JOIN raw_files rf ON rf.id = previous.raw_file_id
            WHERE previous.id <> %s
              AND previous.status = 'completed'
              AND previous.rows_fact_published > 0
              AND previous.regional_scope IS NOT DISTINCT FROM (
                  SELECT regional_scope FROM ingestion_runs WHERE id = %s
              )
              AND rf.fecha_datos <= TO_DATE(%s::text, 'YYYYMMDD')
              AND EXISTS (
                  SELECT 1 FROM fact_precios f
                  WHERE f.run_id = previous.id
                    AND f.fecha_id = TO_CHAR(rf.fecha_datos, 'YYYYMMDD')::int
              )
            ORDER BY rf.fecha_datos DESC, previous.id DESC
            LIMIT 1
            """,
            (run_id, run_id, fecha_id),
        )
        row = cur.fetchone()
    return row[0] if row else None


def _swap_daily_partition(
    conn, *, run_id: int, fecha_id: int, source_fecha_id: int | None
) -> tuple[float, float, int, int]:
    """Build a detached daily leaf, then replace the visible leaf atomically."""
    current_partition = _ensure_partition(conn, fecha_id)
    canonical_partition = f"fact_precios_{fecha_id}"
    monthly_partition = f"fact_precios_{fecha_id // 100:06d}"
    candidate = f"{canonical_partition}_run_{run_id}"
    retired_partition = f"{canonical_partition}_replaced_{run_id}"
    with conn.cursor() as cur:
        started_at = perf_counter()
        # Load without indexes: ATTACH PARTITION creates and attaches the
        # matching inherited indexes after the bulk insert, while this table
        # is still invisible to readers.
        cur.execute(f"CREATE TABLE {candidate} (LIKE {current_partition})")
        cur.execute(
            f"ALTER TABLE {candidate} ADD CONSTRAINT {candidate}_fecha_check "
            f"CHECK (fecha_id = {fecha_id})"
        )
        cur.execute(
            f"""
            INSERT INTO {candidate} (
                producto_sk, sucursal_sk, fecha_id, run_id,
                precio_lista, precio_referencia, precio_promo1, precio_promo2,
                leyenda_promo1, leyenda_promo2, tiene_promo, fecha_observada_id
            )
            SELECT
                producto_sk, sucursal_sk, fecha_id, run_id,
                precio_lista, precio_referencia, precio_promo1, precio_promo2,
                leyenda_promo1, leyenda_promo2, tiene_promo, fecha_id
            FROM pending_fact_precios
            WHERE run_id = %s AND fecha_id = %s
            """,
            (run_id, fecha_id),
        )
        rows_source = cur.rowcount
        rows_carried = 0
        if source_fecha_id is not None:
            cur.execute(
                f"""
                WITH present_stores AS MATERIALIZED (
                    SELECT DISTINCT sucursal_sk
                    FROM pending_fact_precios
                    WHERE run_id = %s
                )
                INSERT INTO {candidate} (
                    producto_sk, sucursal_sk, fecha_id, run_id,
                    precio_lista, precio_referencia, precio_promo1, precio_promo2,
                    leyenda_promo1, leyenda_promo2, tiene_promo, fecha_observada_id
                )
                SELECT
                    old.producto_sk, old.sucursal_sk, %s, %s,
                    old.precio_lista, old.precio_referencia,
                    NULL, NULL, NULL, NULL, FALSE,
                    COALESCE(old.fecha_observada_id, old.fecha_id)
                FROM fact_precios old
                LEFT JOIN present_stores present
                  ON present.sucursal_sk = old.sucursal_sk
                WHERE old.fecha_id = %s
                  AND present.sucursal_sk IS NULL
                """,
                (run_id, fecha_id, run_id, source_fecha_id),
            )
            rows_carried = cur.rowcount
        insert_facts_seconds = perf_counter() - started_at

        started_at = perf_counter()
        cur.execute(f"ALTER TABLE {monthly_partition} DETACH PARTITION {current_partition}")
        cur.execute(f"ALTER TABLE {current_partition} RENAME TO {retired_partition}")
        cur.execute(f"ALTER TABLE {candidate} RENAME TO {canonical_partition}")
        cur.execute(
            f"ALTER TABLE {monthly_partition} ATTACH PARTITION {canonical_partition} "
            f"FOR VALUES FROM ({fecha_id}) TO ({fecha_id + 1})"
        )
        cur.execute(f"DROP TABLE {retired_partition}")
        partition_swap_seconds = perf_counter() - started_at
    return partition_swap_seconds, insert_facts_seconds, rows_source, rows_carried
