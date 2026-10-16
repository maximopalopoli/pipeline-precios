from datetime import datetime, timezone

import pytest

from benchmarks.summary import summarize_chunk_benchmarks
from benchmarks.run_baseline import RunBaseline


def _baseline(*, chunk_size: int, elapsed: float, fact_load: float, rss: int) -> RunBaseline:
    return RunBaseline(
        run_id=chunk_size + rss,
        is_partial=True,
        started_at=datetime(2026, 9, 22, tzinfo=timezone.utc).isoformat(),
        finished_at=datetime(2026, 9, 22, tzinfo=timezone.utc).isoformat(),
        elapsed_seconds=elapsed,
        source_size_bytes=1,
        fact_chunk_size=chunk_size,
        fact_load_elapsed_seconds=fact_load,
        fact_load_peak_rss_bytes=rss,
        fact_load_chunks=1,
        rows_productos_source=1,
        rows_productos_selected=1,
        rows_productos_raw=1,
        rows_productos_valid=1,
        rows_productos_rejected=0,
        rows_fact_pending=1,
        rows_fact_published=0,
        staged_rows_per_second=1.0,
    )


def test_summarize_chunk_benchmarks_reports_medians_and_memory_range():
    summaries = summarize_chunk_benchmarks([
        _baseline(chunk_size=10_000, elapsed=11, fact_load=5, rss=300),
        _baseline(chunk_size=5_000, elapsed=8, fact_load=3, rss=100),
        _baseline(chunk_size=5_000, elapsed=10, fact_load=4, rss=200),
        _baseline(chunk_size=5_000, elapsed=9, fact_load=3.5, rss=150),
    ])

    assert [summary.fact_chunk_size for summary in summaries] == [5_000, 10_000]
    assert summaries[0].sample_count == 3
    assert summaries[0].median_elapsed_seconds == 9.0
    assert summaries[0].median_fact_load_elapsed_seconds == 3.5
    assert summaries[0].median_fact_load_peak_rss_bytes == 150
    assert summaries[0].min_fact_load_peak_rss_bytes == 100
    assert summaries[0].max_fact_load_peak_rss_bytes == 200


def test_summarize_chunk_benchmarks_rejects_incomplete_telemetry():
    incomplete = _baseline(chunk_size=5_000, elapsed=8, fact_load=3, rss=100)
    incomplete = incomplete.__class__(**{**incomplete.as_dict(), "fact_load_chunks": None})

    with pytest.raises(ValueError, match="complete fact-load telemetry"):
        summarize_chunk_benchmarks([incomplete])
