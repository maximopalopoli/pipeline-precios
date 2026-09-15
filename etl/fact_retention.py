"""Safe, partition-level retention for detailed price facts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

import structlog

log = structlog.get_logger(__name__)

_PARTITION_NAME = re.compile(r"^fact_precios_(\d{4})(\d{2})$")


@dataclass(frozen=True)
class FactRetentionResult:
    """Partitions selected for retention and whether they were dropped."""

    partitions: tuple[str, ...]
    dropped: bool


def find_expired_fact_partitions(conn, *, cutoff: date) -> tuple[str, ...]:
    """Return whole monthly fact partitions ending on or before ``cutoff``.

    A partially overlapping month is deliberately retained.  Dropping only
    whole child tables makes the operation fast and recoverable from the
    original ZIPs, without issuing a large row-by-row delete or a VACUUM.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT child.relname
            FROM pg_inherits inheritance
            JOIN pg_class parent ON parent.oid = inheritance.inhparent
            JOIN pg_class child ON child.oid = inheritance.inhrelid
            JOIN pg_namespace namespace ON namespace.oid = child.relnamespace
            WHERE parent.relname = 'fact_precios'
              AND namespace.nspname = 'public'
              AND child.relname ~ '^fact_precios_[0-9]{6}$'
            ORDER BY child.relname
            """
        )
        names = [row[0] for row in cur.fetchall()]

    return tuple(name for name in names if _partition_end(name) <= cutoff)


def purge_expired_fact_partitions(
    conn,
    *,
    cutoff: date,
    dry_run: bool = True,
) -> FactRetentionResult:
    """Optionally drop expired whole-month partitions selected by ``cutoff``.

    ``dry_run`` defaults to true so callers must explicitly opt into a
    destructive operation.  The caller owns the transaction and must commit
    after a non-dry run.
    """
    partitions = find_expired_fact_partitions(conn, cutoff=cutoff)
    if dry_run or not partitions:
        return FactRetentionResult(partitions=partitions, dropped=False)

    with conn.cursor() as cur:
        for partition in partitions:
            # Names come from PostgreSQL's catalog and are checked again before
            # being interpolated as identifiers.
            if _PARTITION_NAME.fullmatch(partition) is None:
                raise ValueError(f"Unexpected fact partition name: {partition}")
            cur.execute(f"DROP TABLE {partition}")

    result = FactRetentionResult(partitions=partitions, dropped=True)
    log.info("retention.fact_partitions_dropped", cutoff=str(cutoff), **result.__dict__)
    return result


def _partition_end(partition: str) -> date:
    match = _PARTITION_NAME.fullmatch(partition)
    if match is None:
        raise ValueError(f"Unexpected fact partition name: {partition}")
    year, month = (int(value) for value in match.groups())
    if not 1 <= month <= 12:
        raise ValueError(f"Unexpected fact partition month: {partition}")
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
    return date(next_year, next_month, 1)
