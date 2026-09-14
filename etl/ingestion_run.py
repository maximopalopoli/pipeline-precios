"""Shared lifecycle operations for an ingestion of one raw file."""

from __future__ import annotations

import structlog

log = structlog.get_logger(__name__)


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


def open_ingestion_run(conn, raw_file_id: int) -> int:
    """Create and commit a running ingestion record for *raw_file_id*."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ingestion_runs (raw_file_id, status) VALUES (%s, 'running') RETURNING id",
            (raw_file_id,),
        )
        run_id: int = cur.fetchone()[0]
    conn.commit()
    return run_id


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
