"""Complete rejection counts per ingestion run, source and reason.

``rejection_counts`` keeps the full totals per stable reason code, so its size
depends only on the number of distinct reasons.  ``rejected_rows`` keeps at
most ``SEPA_REJECTED_SAMPLE_ROWS`` example rows per run, source and code for
diagnosis; the complete rows can be recovered by replaying the archived ZIP.
"""

from __future__ import annotations

import os
from typing import Iterable, Mapping

import structlog

log = structlog.get_logger(__name__)

_SAMPLE_ROWS_ENV = "SEPA_REJECTED_SAMPLE_ROWS"
DEFAULT_SAMPLE_ROWS = 100

# (code, raw_data, detailed reason) of one rejected row
RejectedSample = tuple[str, str, str]


def sample_rows_from_environment(env: Mapping[str, str] | None = None) -> int:
    """Return the sample size per run, source and code (0 keeps no rows)."""
    values = os.environ if env is None else env
    configured = values.get(_SAMPLE_ROWS_ENV, "").strip()
    if not configured:
        return DEFAULT_SAMPLE_ROWS
    try:
        sample_rows = int(configured)
    except ValueError:
        raise ValueError(f"{_SAMPLE_ROWS_ENV} must be a whole number") from None
    if sample_rows < 0:
        raise ValueError(f"{_SAMPLE_ROWS_ENV} must be zero or positive")
    return sample_rows


def add_rejection_counts(
    conn,
    *,
    run_id: int,
    source_file: str,
    counts: Mapping[str, int],
) -> int:
    """Add ``counts`` (reason → rows) to the totals of one run and source.

    Adding is cumulative, so chunked processing can call it once per chunk.
    Zero counts are ignored.  The caller owns the transaction.  Returns the
    number of rows added.
    """
    for reason, row_count in counts.items():
        if row_count < 0:
            raise ValueError(f"Rejection count for {reason!r} must be nonnegative")
    rows = [
        (run_id, source_file, reason, row_count)
        for reason, row_count in sorted(counts.items())
        if row_count > 0
    ]
    if not rows:
        return 0

    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO rejection_counts (run_id, source_file, rejection_reason, row_count)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (run_id, source_file, rejection_reason)
            DO UPDATE SET row_count = rejection_counts.row_count + EXCLUDED.row_count
            """,
            rows,
        )
    total = sum(row[3] for row in rows)
    log.info(
        "rejection_counts.added",
        run_id=run_id,
        source_file=source_file,
        reasons=len(rows),
        count=total,
    )
    return total


def rejection_counts_for_run(conn, run_id: int) -> dict[tuple[str, str], int]:
    """Return ``{(source_file, rejection_reason): row_count}`` for one run."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT source_file, rejection_reason, row_count
            FROM rejection_counts
            WHERE run_id = %s
            """,
            (run_id,),
        )
        return {(source, reason): count for source, reason, count in cur.fetchall()}


def clear_rejections(conn, *, run_id: int, source_files: Iterable[str]) -> None:
    """Remove the counts and samples of ``source_files`` for one run.

    A step that recomputes its rejections from staging calls this first, so a
    retried task with the same run does not count the same rows twice.  The
    caller owns the transaction.
    """
    sources = list(source_files)
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM rejection_counts WHERE run_id = %s AND source_file = ANY(%s)",
            (run_id, sources),
        )
        cur.execute(
            "DELETE FROM rejected_rows WHERE run_id = %s AND source_file = ANY(%s)",
            (run_id, sources),
        )


def record_rejections(
    conn,
    *,
    run_id: int,
    source_file: str,
    counts: Mapping[str, int],
    samples: Iterable[RejectedSample],
    sample_rows: int | None = None,
) -> int:
    """Add complete ``counts`` and store a bounded sample of their rows.

    ``samples`` may be longer than the sample; it is consumed only until each
    code has its ``sample_rows`` rows for this run and source, counting rows
    stored by earlier chunks.  A sample row whose code is not in ``counts`` is
    an error, so every stored row is also counted.  The caller owns the
    transaction.  Returns the number of rows counted.
    """
    if sample_rows is None:
        sample_rows = sample_rows_from_environment()
    already_counted = {
        reason: count
        for (source, reason), count in rejection_counts_for_run(conn, run_id).items()
        if source == source_file
    }
    remaining = {
        code: min(count, max(0, sample_rows - already_counted.get(code, 0)))
        for code, count in counts.items()
        if count > 0
    }
    kept: list[tuple[int, str, str, str, str]] = []
    if any(remaining.values()):
        for code, raw_data, reason in samples:
            if code not in remaining:
                raise ValueError(f"Sampled rejection code {code!r} is not counted")
            if remaining[code] == 0:
                continue
            remaining[code] -= 1
            kept.append((run_id, source_file, code, raw_data, reason))
            if not any(remaining.values()):
                break
    if kept:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO rejected_rows
                    (run_id, source_file, rejection_code, raw_data, rejection_reason)
                VALUES (%s, %s, %s, %s, %s)
                """,
                kept,
            )
    total = add_rejection_counts(
        conn, run_id=run_id, source_file=source_file, counts=counts
    )
    log.info(
        "rejections.recorded",
        run_id=run_id,
        source_file=source_file,
        count=total,
        sampled=len(kept),
    )
    return total
