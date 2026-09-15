"""Persisted audit events for completed retention cleanup."""

from __future__ import annotations

import json
from datetime import date
from typing import Literal, Sequence

RetentionKind = Literal["fact_partitions", "staging_runs", "raw_files"]


def record_retention_event(
    conn,
    *,
    kind: RetentionKind,
    cutoff: date,
    targets: Sequence[str | int],
) -> int:
    """Record the exact targets removed by one committed cleanup operation."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO retention_events (kind, cutoff, targets)
            VALUES (%s, %s, %s::jsonb)
            RETURNING id
            """,
            (kind, cutoff, json.dumps(list(targets))),
        )
        event_id: int = cur.fetchone()[0]
    return event_id
