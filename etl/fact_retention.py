"""Safe, partition-level retention for detailed price facts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

import structlog

from etl.retention_policy import RetentionPolicy
from etl.retention_audit import record_retention_event

log = structlog.get_logger(__name__)

_MONTHLY_PARTITION_NAME = re.compile(r"^fact_precios_(\d{4})(\d{2})$")
_DAILY_PARTITION_NAME = re.compile(r"^fact_precios_(\d{4})(\d{2})(\d{2})$")


@dataclass(frozen=True)
class FactRetentionResult:
    """Partitions selected for retention and whether they were dropped."""

    partitions: tuple[str, ...]
    dropped: bool


def apply_fact_retention(
    conn,
    *,
    policy: RetentionPolicy,
    fecha_datos: date,
) -> FactRetentionResult | None:
    """Apply an enabled fact-retention policy after publication commits.

    The caller owns the transaction.  This intentionally does nothing when
    retention is disabled, keeping the destructive action opt-in.
    """
    cutoff = policy.fact_cutoff(fecha_datos)
    if cutoff is None:
        return None
    result = purge_expired_fact_partitions(conn, cutoff=cutoff, dry_run=False)
    conn.commit()
    return result


def find_expired_fact_partitions(conn, *, cutoff: date) -> tuple[str, ...]:
    """Return fact partitions whose whole range ends on or before ``cutoff``.

    Daily leaves dated before ``cutoff`` come first, then monthly parents that
    are entirely expired, so dropping in order never targets a missing table.
    A monthly parent that still holds a retained day is kept.  Dropping whole
    tables is fast and recoverable from the original ZIPs, without a large
    row-by-row delete or a VACUUM.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT monthly.relname, daily.relname
            FROM pg_inherits monthly_link
            JOIN pg_class parent ON parent.oid = monthly_link.inhparent
            JOIN pg_class monthly ON monthly.oid = monthly_link.inhrelid
            JOIN pg_namespace namespace ON namespace.oid = monthly.relnamespace
            LEFT JOIN pg_inherits daily_link ON daily_link.inhparent = monthly.oid
            LEFT JOIN pg_class daily
              ON daily.oid = daily_link.inhrelid
             AND daily.relname ~ '^fact_precios_[0-9]{8}$'
            WHERE parent.relname = 'fact_precios'
              AND namespace.nspname = 'public'
              AND monthly.relname ~ '^fact_precios_[0-9]{6}$'
            ORDER BY monthly.relname, daily.relname
            """
        )
        rows = cur.fetchall()

    monthly_names = sorted({monthly for monthly, _ in rows})
    daily_names = sorted(daily for _, daily in rows if daily is not None)
    expired_days = [name for name in daily_names if _daily_partition_end(name) <= cutoff]
    expired_months = [
        name for name in monthly_names if _monthly_partition_end(name) <= cutoff
    ]
    return tuple(expired_days + expired_months)


def purge_expired_fact_partitions(
    conn,
    *,
    cutoff: date,
    dry_run: bool = True,
) -> FactRetentionResult:
    """Optionally drop the expired daily and monthly partitions before ``cutoff``.

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
            if (
                _DAILY_PARTITION_NAME.fullmatch(partition) is None
                and _MONTHLY_PARTITION_NAME.fullmatch(partition) is None
            ):
                raise ValueError(f"Unexpected fact partition name: {partition}")
            cur.execute(f"DROP TABLE {partition}")

    record_retention_event(
        conn,
        kind="fact_partitions",
        cutoff=cutoff,
        targets=partitions,
    )
    result = FactRetentionResult(partitions=partitions, dropped=True)
    log.info("retention.fact_partitions_dropped", cutoff=str(cutoff), **result.__dict__)
    return result


def _daily_partition_end(partition: str) -> date:
    match = _DAILY_PARTITION_NAME.fullmatch(partition)
    if match is None:
        raise ValueError(f"Unexpected fact partition name: {partition}")
    year, month, day = (int(value) for value in match.groups())
    try:
        return date(year, month, day) + timedelta(days=1)
    except ValueError as exc:
        raise ValueError(f"Unexpected fact partition day: {partition}") from exc


def _monthly_partition_end(partition: str) -> date:
    match = _MONTHLY_PARTITION_NAME.fullmatch(partition)
    if match is None:
        raise ValueError(f"Unexpected fact partition name: {partition}")
    year, month = (int(value) for value in match.groups())
    if not 1 <= month <= 12:
        raise ValueError(f"Unexpected fact partition month: {partition}")
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
    return date(next_year, next_month, 1)
