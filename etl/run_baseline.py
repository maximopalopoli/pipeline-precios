"""Read comparable, persisted ETL baselines from ``ingestion_runs``.

The pipeline deliberately keeps its per-stage telemetry in structured logs: a
single Airflow run can execute those stages in different workers. This module
reports durable run-level data instead of reconstructing timings from logs.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class RunBaseline:
    """Durable volume and end-to-end timing measurements for one run."""

    run_id: int
    is_partial: bool
    started_at: str
    finished_at: str
    elapsed_seconds: float | None
    source_size_bytes: int
    rows_productos_source: int | None
    rows_productos_selected: int | None
    rows_productos_raw: int | None
    rows_productos_valid: int | None
    rows_productos_rejected: int | None
    rows_fact_pending: int | None
    rows_fact_published: int | None
    staged_rows_per_second: float | None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


_BASELINE_SQL = """
    SELECT
        run.id, run.is_partial, run.started_at, run.finished_at,
        run.duration_seconds, raw.size_bytes, run.rows_productos_source,
        run.rows_productos_selected, run.rows_productos_raw,
        run.rows_productos_valid, run.rows_productos_rejected,
        run.rows_fact_pending, run.rows_fact_published
    FROM ingestion_runs AS run
    JOIN raw_files AS raw ON raw.id = run.raw_file_id
    WHERE run.id = %s
      AND run.status = 'completed'
"""


def load_run_baseline(conn: Any, *, run_id: int) -> RunBaseline | None:
    """Return a completed run's durable baseline, if it exists."""
    with conn.cursor() as cur:
        cur.execute(_BASELINE_SQL, (run_id,))
        row = cur.fetchone()
    if row is None:
        return None
    (
        found_run_id, is_partial, started_at, finished_at, duration_seconds,
        source_size_bytes, rows_source, rows_selected, rows_raw, rows_valid,
        rows_rejected, rows_pending, rows_published,
    ) = row
    elapsed = _as_float(duration_seconds)
    staged_rows_per_second = (
        round(int(rows_raw) / elapsed, 3)
        if rows_raw is not None and elapsed is not None and elapsed > 0
        else None
    )
    return RunBaseline(
        run_id=int(found_run_id), is_partial=bool(is_partial),
        started_at=started_at.isoformat(), finished_at=finished_at.isoformat(),
        elapsed_seconds=elapsed, source_size_bytes=int(source_size_bytes),
        rows_productos_source=_as_int(rows_source),
        rows_productos_selected=_as_int(rows_selected),
        rows_productos_raw=_as_int(rows_raw), rows_productos_valid=_as_int(rows_valid),
        rows_productos_rejected=_as_int(rows_rejected),
        rows_fact_pending=_as_int(rows_pending),
        rows_fact_published=_as_int(rows_published),
        staged_rows_per_second=staged_rows_per_second,
    )


def latest_completed_run_id(conn: Any) -> int | None:
    """Return the most recent completed run, including bounded local runs."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id FROM ingestion_runs
            WHERE status = 'completed'
            ORDER BY finished_at DESC, id DESC
            LIMIT 1
            """
        )
        row = cur.fetchone()
    return int(row[0]) if row is not None else None


def _as_int(value: object) -> int | None:
    return int(value) if value is not None else None


def _as_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return float(value)
