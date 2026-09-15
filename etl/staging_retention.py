"""Safe cleanup of terminal ingestion staging data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import structlog

from etl.retention_policy import RetentionPolicy

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class StagingRetentionResult:
    """Terminal runs and staging rows selected for cleanup."""

    run_ids: tuple[int, ...]
    rows_comercios: int
    rows_sucursales: int
    rows_productos_default: int
    dropped_product_partitions: int
    dropped: bool


def find_expired_staging_runs(conn, *, cutoff: date) -> tuple[int, ...]:
    """Return completed or failed runs finished before ``cutoff``.

    Running runs are excluded unconditionally, even when their start time is
    old.  They may still be retried or be owned by an active scheduler task.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id
            FROM ingestion_runs
            WHERE status IN ('completed', 'failed')
              AND finished_at < %s
            ORDER BY id
            """,
            (cutoff,),
        )
        return tuple(row[0] for row in cur.fetchall())


def purge_expired_staging_runs(
    conn,
    *,
    cutoff: date,
    dry_run: bool = True,
) -> StagingRetentionResult:
    """Optionally remove staging data for terminal runs before ``cutoff``.

    Product partitions are dropped instead of deleted.  Rows that might have
    landed in the default partition are deleted explicitly. The caller owns
    the transaction and must commit after a non-dry run.
    """
    run_ids = find_expired_staging_runs(conn, cutoff=cutoff)
    if dry_run or not run_ids:
        return StagingRetentionResult(run_ids, 0, 0, 0, 0, False)

    rows_comercios = 0
    rows_sucursales = 0
    rows_productos_default = 0
    dropped_product_partitions = 0
    with conn.cursor() as cur:
        for run_id in run_ids:
            cur.execute("DELETE FROM stg_comercios WHERE run_id = %s", (run_id,))
            rows_comercios += cur.rowcount
            cur.execute("DELETE FROM stg_sucursales WHERE run_id = %s", (run_id,))
            rows_sucursales += cur.rowcount
            cur.execute("DELETE FROM stg_productos_default WHERE run_id = %s", (run_id,))
            rows_productos_default += cur.rowcount

            partition_name = f"stg_productos_{run_id}"
            cur.execute("SELECT to_regclass(%s)", (f"public.{partition_name}",))
            if cur.fetchone()[0] is not None:
                # run_id is an integer selected by PostgreSQL, so this
                # identifier cannot be influenced by user input.
                cur.execute(f"DROP TABLE {partition_name}")
                dropped_product_partitions += 1

    result = StagingRetentionResult(
        run_ids,
        rows_comercios,
        rows_sucursales,
        rows_productos_default,
        dropped_product_partitions,
        True,
    )
    log.info("retention.staging_dropped", cutoff=str(cutoff), **result.__dict__)
    return result


def apply_staging_retention(
    conn,
    *,
    policy: RetentionPolicy,
    fecha_datos: date,
) -> StagingRetentionResult | None:
    """Apply an enabled staging-retention policy after publication commits."""
    cutoff = policy.staging_cutoff(fecha_datos)
    if cutoff is None:
        return None
    result = purge_expired_staging_runs(conn, cutoff=cutoff, dry_run=False)
    conn.commit()
    return result
