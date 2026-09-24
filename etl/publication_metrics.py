"""Persist detailed timings for atomic full publications."""

from __future__ import annotations


def record_publication_metrics(
    conn,
    *,
    run_id: int,
    delete_existing_seconds: float,
    insert_facts_seconds: float,
    aggregate_delete_seconds: float,
    aggregate_insert_seconds: float,
    cleanup_seconds: float,
    partition_swap_seconds: float = 0,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO ingestion_publication_metrics (
                run_id, delete_existing_seconds, insert_facts_seconds,
                aggregate_delete_seconds, aggregate_insert_seconds, cleanup_seconds
                , partition_swap_seconds
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (run_id) DO UPDATE SET
                delete_existing_seconds = EXCLUDED.delete_existing_seconds,
                insert_facts_seconds = EXCLUDED.insert_facts_seconds,
                aggregate_delete_seconds = EXCLUDED.aggregate_delete_seconds,
                aggregate_insert_seconds = EXCLUDED.aggregate_insert_seconds,
                cleanup_seconds = EXCLUDED.cleanup_seconds,
                partition_swap_seconds = EXCLUDED.partition_swap_seconds,
                recorded_at = NOW()
            """,
            (
                run_id,
                delete_existing_seconds,
                insert_facts_seconds,
                aggregate_delete_seconds,
                aggregate_insert_seconds,
                cleanup_seconds,
                partition_swap_seconds,
            ),
        )
