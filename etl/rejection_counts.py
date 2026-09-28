"""Complete rejection counts per ingestion run, source and reason.

``rejected_rows`` grows with every rejected row of every run.  These counts
keep the full totals in a table whose size depends only on the number of
distinct reasons, so the stored rows can become a bounded sample.
"""

from __future__ import annotations

from typing import Mapping

import structlog

log = structlog.get_logger(__name__)


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
