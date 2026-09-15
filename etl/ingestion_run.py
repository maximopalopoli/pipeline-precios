"""Shared lifecycle operations for an ingestion of one raw file."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

import structlog

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

    @classmethod
    def completed(cls, rows_loaded: int) -> IngestionResult:
        return cls(outcome=IngestionOutcome.COMPLETED, rows_loaded=rows_loaded)

    @classmethod
    def skipped(cls) -> IngestionResult:
        return cls(outcome=IngestionOutcome.SKIPPED)


def has_completed_run(conn, raw_file_id: int) -> bool:
    """Return whether *raw_file_id* has already been published successfully."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM ingestion_runs WHERE raw_file_id = %s AND status = 'completed'",
            (raw_file_id,),
        )
        return cur.fetchone() is not None


def should_process_raw_file(conn, raw_file_id: int) -> bool:
    """Return whether a raw file is eligible for an ingestion attempt.

    Failed and interrupted attempts are retried.  A completed publication is
    the only terminal state that skips a duplicate raw file.
    """
    return not has_completed_run(conn, raw_file_id)


def open_ingestion_run(
    conn,
    raw_file_id: int,
    *,
    fecha_solicitada: date | None = None,
    fecha_logica: date | None = None,
) -> int:
    """Create a running record and persist the dates known at its opening.

    Legacy callers may omit the dates; in that case the raw-file date supplies
    both values.  Entrypoints should pass them explicitly so a future source
    date discrepancy remains traceable.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO ingestion_runs (
                raw_file_id, status, fecha_solicitada, fecha_logica
            )
            SELECT
                id,
                'running',
                COALESCE(%s, fecha_datos),
                COALESCE(%s, fecha_datos)
            FROM raw_files
            WHERE id = %s
            RETURNING id
            """,
            (fecha_solicitada, fecha_logica, raw_file_id),
        )
        row = cur.fetchone()
    if row is None:
        raise ValueError(f"Raw file {raw_file_id} does not exist.")
    run_id: int = row[0]
    conn.commit()
    return run_id


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
