"""Small PostgreSQL storage snapshots for comparable ingestion baselines."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StorageFootprint:
    """Bytes occupied by the database and the ingestion's main relations."""

    database_bytes: int
    fact_precios_bytes: int
    pending_fact_precios_bytes: int
    staging_comercios_bytes: int
    staging_sucursales_bytes: int
    staging_productos_bytes: int


def collect_storage_footprint(conn, *, run_id: int) -> StorageFootprint:
    """Measure storage relevant to one run without scanning product rows.

    PostgreSQL catalog size functions include indexes and TOAST storage. The
    run-specific product staging partition may not exist after cleanup, so its
    size is reported as zero in that case.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                pg_database_size(current_database()),
                pg_total_relation_size('fact_precios'::regclass),
                pg_total_relation_size('pending_fact_precios'::regclass),
                pg_total_relation_size('stg_comercios'::regclass),
                pg_total_relation_size('stg_sucursales'::regclass),
                COALESCE(
                    pg_total_relation_size(
                        to_regclass(format('public.stg_productos_%s', %s))
                    ),
                    0
                )
            """,
            (run_id,),
        )
        row = cur.fetchone()
    return StorageFootprint(*(int(value) for value in row))
