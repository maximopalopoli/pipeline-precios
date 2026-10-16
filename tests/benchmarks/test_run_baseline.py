import json
from datetime import datetime, timezone
from decimal import Decimal

from benchmarks.run_baseline import load_run_baseline, read_stage_timings


class _Cursor:
    def __init__(self, row):
        self.row = row
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def execute(self, sql, params):
        self.calls.append((sql, params))

    def fetchone(self):
        return self.row


class _Connection:
    def __init__(self, row):
        self.cursor_instance = _Cursor(row)

    def cursor(self):
        return self.cursor_instance


def _write_log(path, *events):
    path.write_text("".join(json.dumps(event) + "\n" for event in events))


def test_load_run_baseline_joins_durable_counts_with_logged_stage_timings(tmp_path):
    _write_log(
        tmp_path / "ingest-20260922-120000.log",
        {"event": "pipeline.stage.done", "stage": "fact_load", "run_id": 7,
         "elapsed_seconds": 2.0, "peak_rss_bytes": 123_456, "chunks": 1},
        {"event": "publisher.pending_facts_published", "run_id": 7,
         "insert_facts_seconds": 3.0, "partition_swap_seconds": 0.5},
    )
    conn = _Connection((
        7, True, datetime(2026, 9, 22, 12, tzinfo=timezone.utc),
        datetime(2026, 9, 22, 12, 0, 4, tzinfo=timezone.utc), Decimal("4.0"),
        321_841_103, 50_000,
        1_048_533, 10_000, 5_000, 4_900, 100, 4_900, 0,
    ))

    baseline = load_run_baseline(conn, run_id=7, log_dir=tmp_path)

    assert baseline is not None
    assert baseline.run_id == 7
    assert baseline.is_partial is True
    assert baseline.rows_productos_source == 1_048_533
    assert baseline.fact_chunk_size == 50_000
    assert baseline.rows_productos_raw == 5_000
    assert baseline.fact_load_elapsed_seconds == 2.0
    assert baseline.fact_load_peak_rss_bytes == 123_456
    assert baseline.fact_load_chunks == 1
    assert baseline.staged_rows_per_second == 1250.0
    assert baseline.publication_insert_facts_seconds == 3.0
    assert baseline.publication_partition_swap_seconds == 0.5
    assert conn.cursor_instance.calls[0][1] == (7,)


def test_stage_timings_come_from_the_newest_log_of_the_run(tmp_path):
    _write_log(
        tmp_path / "ingest-20260922-120000.log",
        {"event": "pipeline.stage.done", "stage": "fact_load", "run_id": 7,
         "elapsed_seconds": 9.0},
    )
    (tmp_path / "ingest-20260923-120000.log").write_text(
        "2026-09-23T12:00:00Z [info] an older console line\n"
        + json.dumps({"event": "pipeline.stage.done", "stage": "fact_load",
                      "run_id": 7, "elapsed_seconds": 2.0}) + "\n"
        + json.dumps({"event": "pipeline.stage.done", "stage": "fact_load",
                      "run_id": 8, "elapsed_seconds": 5.0}) + "\n"
    )

    assert read_stage_timings(run_id=7, log_dir=tmp_path).fact_load_elapsed_seconds == 2.0


def test_stage_timings_are_empty_without_a_log_of_the_run(tmp_path):
    timings = read_stage_timings(run_id=7, log_dir=tmp_path)

    assert timings.fact_load_elapsed_seconds is None
    assert timings.publication_insert_facts_seconds is None
