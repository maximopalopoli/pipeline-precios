"""Persist performance telemetry for the streamed fact-load stage."""

from __future__ import annotations

from etl.performance import process_max_rss_bytes


def record_fact_load_metrics(
    conn,
    *,
    run_id: int,
    elapsed_seconds: float,
    chunks: int,
) -> None:
    """Upsert the terminal measurement for a run's fact-load stage.

    The metric is replaced rather than duplicated if the stage is recorded
    again for the same run.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO ingestion_fact_load_metrics (
                run_id, elapsed_seconds, peak_rss_bytes, chunks
            )
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (run_id) DO UPDATE
               SET elapsed_seconds = EXCLUDED.elapsed_seconds,
                   peak_rss_bytes = EXCLUDED.peak_rss_bytes,
                   chunks = EXCLUDED.chunks,
                   recorded_at = NOW()
            """,
            (run_id, elapsed_seconds, process_max_rss_bytes(), chunks),
        )
