"""Summarize reproducible, persisted ETL benchmark samples."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import median
from typing import Iterable

from etl.run_baseline import RunBaseline


@dataclass(frozen=True)
class ChunkBenchmarkSummary:
    """Median and range of samples taken with one fact chunk size."""

    fact_chunk_size: int
    sample_count: int
    median_elapsed_seconds: float
    median_fact_load_elapsed_seconds: float
    median_fact_load_peak_rss_bytes: int
    min_fact_load_peak_rss_bytes: int
    max_fact_load_peak_rss_bytes: int

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def summarize_chunk_benchmarks(
    baselines: Iterable[RunBaseline],
) -> list[ChunkBenchmarkSummary]:
    """Group complete fact-load samples by chunk size and report medians.

    A sample without complete fact-load telemetry is rejected rather than
    silently biasing a comparison with data from a different schema version.
    """
    grouped: dict[int, list[RunBaseline]] = {}
    for baseline in baselines:
        if (
            baseline.fact_chunk_size is None
            or baseline.elapsed_seconds is None
            or baseline.fact_load_elapsed_seconds is None
            or baseline.fact_load_peak_rss_bytes is None
            or baseline.fact_load_chunks is None
        ):
            raise ValueError("Benchmarks require complete fact-load telemetry.")
        grouped.setdefault(baseline.fact_chunk_size, []).append(baseline)

    summaries = []
    for chunk_size, samples in grouped.items():
        rss_values = [sample.fact_load_peak_rss_bytes for sample in samples]
        summaries.append(
            ChunkBenchmarkSummary(
                fact_chunk_size=chunk_size,
                sample_count=len(samples),
                median_elapsed_seconds=float(median(
                    sample.elapsed_seconds for sample in samples
                )),
                median_fact_load_elapsed_seconds=float(median(
                    sample.fact_load_elapsed_seconds for sample in samples
                )),
                median_fact_load_peak_rss_bytes=int(median(rss_values)),
                min_fact_load_peak_rss_bytes=min(rss_values),
                max_fact_load_peak_rss_bytes=max(rss_values),
            )
        )
    return sorted(summaries, key=lambda summary: summary.fact_chunk_size)
