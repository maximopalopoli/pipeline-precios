import json
from datetime import date

from etl.development_ingestion import DevelopmentIngestionPolicy
from etl.ingestion_run import open_ingestion_run
from benchmarks.run_baseline import load_comparable_run_baselines


def _raw_file_id(conn) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO raw_files (filename, url, sha256, size_bytes, fecha_datos)
            VALUES ('comparison.zip', 'file:///comparison.zip', 'comparison', 1, %s)
            RETURNING id
            """,
            (date(2026, 3, 17),),
        )
        return cur.fetchone()[0]


def _complete(conn, run_id: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE ingestion_runs SET status = 'completed', finished_at = NOW() WHERE id = %s",
            (run_id,),
        )
    conn.commit()


def _log_fact_load(log_dir, run_id: int, *, elapsed_seconds: float, chunks: int) -> None:
    event = {
        "event": "pipeline.stage.done", "stage": "fact_load", "run_id": run_id,
        "elapsed_seconds": elapsed_seconds, "peak_rss_bytes": 123_456, "chunks": chunks,
    }
    with (log_dir / f"ingest-{run_id:08d}.log").open("a") as log:
        log.write(json.dumps(event) + "\n")


def test_comparison_keeps_only_runs_with_matching_source_and_partial_bounds(
    pg_conn, tmp_path
):
    raw_file_id = _raw_file_id(pg_conn)
    policy = DevelopmentIngestionPolicy(chain_keys=((12, 1),), max_product_rows=5000)
    first = open_ingestion_run(
        pg_conn, raw_file_id, development_policy=policy, fact_chunk_size=10_000
    )
    second = open_ingestion_run(
        pg_conn, raw_file_id, development_policy=policy, fact_chunk_size=50_000
    )
    different_bounds = open_ingestion_run(
        pg_conn,
        raw_file_id,
        development_policy=DevelopmentIngestionPolicy(
            chain_keys=((12, 1),), max_product_rows=10_000
        ),
        fact_chunk_size=100_000,
    )
    full_run = open_ingestion_run(pg_conn, raw_file_id, fact_chunk_size=100_000)
    for run_id in (first, second, different_bounds, full_run):
        _complete(pg_conn, run_id)
    _log_fact_load(tmp_path, first, elapsed_seconds=1.25, chunks=5)
    _log_fact_load(tmp_path, second, elapsed_seconds=2.5, chunks=1)

    baselines = load_comparable_run_baselines(pg_conn, run_id=first, log_dir=tmp_path)

    assert [baseline.run_id for baseline in baselines] == [first, second]
    assert [baseline.fact_chunk_size for baseline in baselines] == [10_000, 50_000]
    assert [baseline.fact_load_elapsed_seconds for baseline in baselines] == [1.25, 2.5]
    assert [baseline.fact_load_peak_rss_bytes for baseline in baselines] == [123_456, 123_456]
    assert [baseline.fact_load_chunks for baseline in baselines] == [5, 1]
