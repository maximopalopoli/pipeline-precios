"""Read comparable ETL baselines without any help from the pipeline.

Volumes and end-to-end timing come from ``ingestion_runs``. Stage timings come
from the run's full log: the CLI writes every event to ``logs/ingest-*.log``
as one JSON object per line, so the pipeline keeps no benchmark tables.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from etl.log_output import LOG_DIR


@dataclass(frozen=True)
class RunBaseline:
    """Durable volume and end-to-end timing measurements for one run."""

    run_id: int
    is_partial: bool
    started_at: str
    finished_at: str
    elapsed_seconds: float | None
    source_size_bytes: int
    fact_chunk_size: int | None
    fact_load_elapsed_seconds: float | None
    fact_load_peak_rss_bytes: int | None
    fact_load_chunks: int | None
    rows_productos_source: int | None
    rows_productos_selected: int | None
    rows_productos_raw: int | None
    rows_productos_valid: int | None
    rows_productos_rejected: int | None
    rows_fact_pending: int | None
    rows_fact_published: int | None
    staged_rows_per_second: float | None
    publication_insert_facts_seconds: float | None = None
    publication_partition_swap_seconds: float | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class StageTimings:
    """Stage measurements found in a run's log; ``None`` when absent."""

    fact_load_elapsed_seconds: float | None = None
    fact_load_peak_rss_bytes: int | None = None
    fact_load_chunks: int | None = None
    publication_insert_facts_seconds: float | None = None
    publication_partition_swap_seconds: float | None = None


_BASELINE_SQL = """
    SELECT
        run.id, run.is_partial, run.started_at, run.finished_at,
        run.duration_seconds, raw.size_bytes, run.fact_chunk_size,
        run.rows_productos_source,
        run.rows_productos_selected, run.rows_productos_raw,
        run.rows_productos_valid, run.rows_productos_rejected,
        run.rows_fact_pending, run.rows_fact_published
    FROM ingestion_runs AS run
    JOIN raw_files AS raw ON raw.id = run.raw_file_id
    WHERE run.id = %s
      AND run.status = 'completed'
"""


_COMPARABLE_BASELINES_SQL = """
    WITH target AS (
        SELECT raw_file_id, is_partial, partial_chain_keys, partial_max_product_rows
        FROM ingestion_runs
        WHERE id = %s
    )
    SELECT run.id
    FROM ingestion_runs AS run
    CROSS JOIN target
    WHERE run.raw_file_id = target.raw_file_id
      AND run.status = 'completed'
      AND run.fact_chunk_size IS NOT NULL
      AND run.is_partial = target.is_partial
      AND run.partial_chain_keys IS NOT DISTINCT FROM target.partial_chain_keys
      AND run.partial_max_product_rows IS NOT DISTINCT FROM target.partial_max_product_rows
    ORDER BY run.fact_chunk_size, run.finished_at, run.id
"""


def load_run_baseline(
    conn: Any, *, run_id: int, log_dir: Path = LOG_DIR
) -> RunBaseline | None:
    """Return a completed run's baseline, if it exists."""
    with conn.cursor() as cur:
        cur.execute(_BASELINE_SQL, (run_id,))
        row = cur.fetchone()
    if row is None:
        return None
    (
        found_run_id, is_partial, started_at, finished_at, duration_seconds,
        source_size_bytes, chunk_size,
        rows_source, rows_selected, rows_raw, rows_valid,
        rows_rejected, rows_pending, rows_published,
    ) = row
    elapsed = _as_float(duration_seconds)
    staged_rows_per_second = (
        round(int(rows_raw) / elapsed, 3)
        if rows_raw is not None and elapsed is not None and elapsed > 0
        else None
    )
    timings = read_stage_timings(run_id=int(found_run_id), log_dir=log_dir)
    return RunBaseline(
        run_id=int(found_run_id), is_partial=bool(is_partial),
        started_at=started_at.isoformat(), finished_at=finished_at.isoformat(),
        elapsed_seconds=elapsed, source_size_bytes=int(source_size_bytes),
        fact_chunk_size=_as_int(chunk_size),
        fact_load_elapsed_seconds=timings.fact_load_elapsed_seconds,
        fact_load_peak_rss_bytes=timings.fact_load_peak_rss_bytes,
        fact_load_chunks=timings.fact_load_chunks,
        rows_productos_source=_as_int(rows_source),
        rows_productos_selected=_as_int(rows_selected),
        rows_productos_raw=_as_int(rows_raw), rows_productos_valid=_as_int(rows_valid),
        rows_productos_rejected=_as_int(rows_rejected),
        rows_fact_pending=_as_int(rows_pending),
        rows_fact_published=_as_int(rows_published),
        staged_rows_per_second=staged_rows_per_second,
        publication_insert_facts_seconds=timings.publication_insert_facts_seconds,
        publication_partition_swap_seconds=timings.publication_partition_swap_seconds,
    )


def read_stage_timings(*, run_id: int, log_dir: Path = LOG_DIR) -> StageTimings:
    """Read a run's fact-load and publication timings from its full log.

    The newest log holding events of the run wins. Lines that are not JSON,
    such as logs written before the JSON format, are ignored.
    """
    for path in sorted(log_dir.glob("ingest-*.log"), reverse=True):
        events = list(_run_events(path, run_id))
        if events:
            return _timings(events)
    return StageTimings()


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


def load_comparable_run_baselines(
    conn: Any, *, run_id: int, log_dir: Path = LOG_DIR
) -> list[RunBaseline]:
    """Return completed runs with the same archive and bounded-run policy.

    This deliberately excludes runs without a recorded batch size or a logged
    fact-load measurement, and runs whose chain or row limits differ. The
    remaining list is appropriate for comparing only the fact-chunk-size
    experiment.
    """
    with conn.cursor() as cur:
        cur.execute(_COMPARABLE_BASELINES_SQL, (run_id,))
        run_ids = [int(row[0]) for row in cur.fetchall()]
    baselines = (
        load_run_baseline(conn, run_id=candidate_id, log_dir=log_dir)
        for candidate_id in run_ids
    )
    return [
        baseline
        for baseline in baselines
        if baseline is not None and baseline.fact_load_elapsed_seconds is not None
    ]


def _run_events(path: Path, run_id: int) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as lines:
        for line in lines:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict) and event.get("run_id") == run_id:
                yield event


def _timings(events: list[dict[str, Any]]) -> StageTimings:
    fact_load: dict[str, Any] = {}
    publication: dict[str, Any] = {}
    for event in events:
        name = event.get("event")
        if name == "pipeline.stage.done" and event.get("stage") == "fact_load":
            fact_load = event
        elif name == "publisher.pending_facts_published":
            publication = event
    return StageTimings(
        fact_load_elapsed_seconds=_as_float(fact_load.get("elapsed_seconds")),
        fact_load_peak_rss_bytes=_as_int(fact_load.get("peak_rss_bytes")),
        fact_load_chunks=_as_int(fact_load.get("chunks")),
        publication_insert_facts_seconds=_as_float(publication.get("insert_facts_seconds")),
        publication_partition_swap_seconds=_as_float(
            publication.get("partition_swap_seconds")
        ),
    )


def _as_int(value: object) -> int | None:
    return int(value) if value is not None else None


def _as_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return float(value)
