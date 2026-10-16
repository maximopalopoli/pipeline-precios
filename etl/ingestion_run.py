"""Shared lifecycle operations for an ingestion of one raw file."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

import structlog

from etl.development_ingestion import DevelopmentIngestionPolicy

log = structlog.get_logger(__name__)


class IngestionOutcome(StrEnum):
    """Terminal non-error outcomes exposed by an ingestion entrypoint."""

    COMPLETED = "completed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class IngestionResult:
    """Result of an ingestion that completed without an operational failure."""

    outcome: IngestionOutcome
    rows_loaded: int = 0
    run_id: int | None = None

    @classmethod
    def completed(cls, rows_loaded: int, *, run_id: int | None = None) -> IngestionResult:
        return cls(
            outcome=IngestionOutcome.COMPLETED,
            rows_loaded=rows_loaded,
            run_id=run_id,
        )

    @classmethod
    def skipped(cls) -> IngestionResult:
        return cls(outcome=IngestionOutcome.SKIPPED)


def has_completed_run(
    conn,
    raw_file_id: int,
    *,
    partial: bool = False,
    regional_scope: str | None = None,
) -> bool:
    """Return whether *raw_file_id* was already published with this scope."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1
            FROM ingestion_runs
            WHERE raw_file_id = %s
              AND status = 'completed'
              AND is_partial = FALSE
              AND regional_scope IS NOT DISTINCT FROM %s
              AND NOT %s
            """,
            (raw_file_id, regional_scope, partial),
        )
        return cur.fetchone() is not None


def should_process_raw_file(
    conn,
    raw_file_id: int,
    *,
    partial: bool = False,
    regional_scope: str | None = None,
) -> bool:
    """Return whether a raw file is eligible for an ingestion attempt.

    Failed and interrupted attempts are retried. A completed *full*
    publication with the same regional scope is the only terminal state that
    skips a full run, so changing the scope re-ingests the file. Partial
    development runs remain eligible so they can inspect a completed source
    without changing its publication status.
    """
    return not has_completed_run(
        conn, raw_file_id, partial=partial, regional_scope=regional_scope
    )


def open_ingestion_run(
    conn,
    raw_file_id: int,
    *,
    fecha_solicitada: date | None = None,
    fecha_logica: date | None = None,
    development_policy: DevelopmentIngestionPolicy | None = None,
    fact_chunk_size: int | None = None,
    regional_scope: str | None = None,
) -> int:
    """Create a running record and persist the dates known at its opening.

    Legacy callers may omit the dates; in that case the raw-file date supplies
    both values.  Entrypoints should pass them explicitly so a future source
    date discrepancy remains traceable.
    """
    development_policy = development_policy or DevelopmentIngestionPolicy()
    partial_chain_keys = ",".join(
        f"{id_comercio}:{id_bandera}"
        for id_comercio, id_bandera in development_policy.chain_keys
    )
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO ingestion_runs (
                raw_file_id, status, fecha_solicitada, fecha_logica,
                is_partial, partial_chain_keys, partial_max_product_rows,
                fact_chunk_size, regional_scope
            )
            SELECT
                id,
                'running',
                COALESCE(%s, fecha_datos),
                COALESCE(%s, fecha_datos),
                %s, %s, %s, %s, %s
            FROM raw_files
            WHERE id = %s
            RETURNING id
            """,
            (
                fecha_solicitada,
                fecha_logica,
                development_policy.enabled,
                partial_chain_keys,
                development_policy.max_product_rows,
                fact_chunk_size,
                regional_scope,
                raw_file_id,
            ),
        )
        row = cur.fetchone()
    if row is None:
        raise ValueError(f"Raw file {raw_file_id} does not exist.")
    run_id: int = row[0]
    delete_superseded_partial_pending(conn, run_id=run_id)
    conn.commit()
    return run_id


def delete_superseded_partial_pending(conn, *, run_id: int) -> int:
    """Delete pending facts of completed partial runs except the newest one.

    A partial run never publishes, so its pending facts would otherwise stay
    forever.  The newest completed partial run keeps its rows for local
    inspection until another partial run completes.  Running and failed runs
    are never touched, so a retry still finds its rows.  The caller owns the
    transaction.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT superseded.id
            FROM (
                SELECT id
                FROM ingestion_runs
                WHERE is_partial = TRUE AND status = 'completed'
                ORDER BY id DESC
                OFFSET 1
            ) AS superseded
            WHERE EXISTS (
                SELECT 1 FROM pending_fact_precios AS pending
                WHERE pending.run_id = superseded.id
            )
            ORDER BY superseded.id
            """
        )
        partial_run_ids = [row[0] for row in cur.fetchall()]
        if not partial_run_ids:
            return 0
        cur.execute(
            "DELETE FROM pending_fact_precios WHERE run_id = ANY(%s)",
            (partial_run_ids,),
        )
        rows_deleted = cur.rowcount
    if rows_deleted:
        log.info(
            "ingestion_run.partial_pending_deleted",
            run_id=run_id,
            partial_run_ids=partial_run_ids,
            rows_deleted=rows_deleted,
        )
    return rows_deleted


def record_source_date(conn, run_id: int, fecha_fuente: date | None) -> None:
    """Record the date declared by the source archive once it is available."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE ingestion_runs
               SET fecha_fuente = %s
             WHERE id = %s
            """,
            (fecha_fuente, run_id),
        )
        if cur.rowcount != 1:
            raise ValueError(f"Ingestion run {run_id} does not exist.")
    conn.commit()


def mark_run_failed(conn, run_id: int, message: str) -> None:
    """Record a terminal failure and its message for an ingestion run."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE ingestion_runs
               SET status        = 'failed',
                   finished_at   = NOW(),
                   error_message = %s
             WHERE id = %s
            """,
            (message, run_id),
        )
    conn.commit()
    log.error("pipeline.run.failed", run_id=run_id)
