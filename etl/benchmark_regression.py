"""Compare reproducible benchmark reports without overreacting to noise."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class BenchmarkRegression:
    """One candidate chunk-size result relative to its baseline."""

    fact_chunk_size: int
    elapsed_regression_pct: float
    peak_rss_regression_pct: float
    exceeds_elapsed_limit: bool
    exceeds_peak_rss_limit: bool

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def compare_benchmark_reports(
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
    *,
    max_elapsed_regression_pct: float,
    max_peak_rss_regression_pct: float,
) -> list[BenchmarkRegression]:
    """Compare medians when source archive, date and bounds are identical."""
    if max_elapsed_regression_pct < 0 or max_peak_rss_regression_pct < 0:
        raise ValueError("Regression limits must not be negative.")
    for field in ("source_archive", "fecha", "policy"):
        if baseline.get(field) != candidate.get(field):
            raise ValueError(f"Benchmark reports differ in {field}.")

    baseline_summaries = _summaries_by_chunk_size(baseline)
    candidate_summaries = _summaries_by_chunk_size(candidate)
    if baseline_summaries.keys() != candidate_summaries.keys():
        raise ValueError("Benchmark reports must contain the same chunk sizes.")

    comparisons = []
    for chunk_size in sorted(baseline_summaries):
        previous = baseline_summaries[chunk_size]
        current = candidate_summaries[chunk_size]
        elapsed_pct = _percent_change(
            current["median_elapsed_seconds"], previous["median_elapsed_seconds"]
        )
        rss_pct = _percent_change(
            current["median_fact_load_peak_rss_bytes"],
            previous["median_fact_load_peak_rss_bytes"],
        )
        comparisons.append(
            BenchmarkRegression(
                fact_chunk_size=chunk_size,
                elapsed_regression_pct=elapsed_pct,
                peak_rss_regression_pct=rss_pct,
                exceeds_elapsed_limit=elapsed_pct > max_elapsed_regression_pct,
                exceeds_peak_rss_limit=rss_pct > max_peak_rss_regression_pct,
            )
        )
    return comparisons


def _summaries_by_chunk_size(report: Mapping[str, Any]) -> dict[int, Mapping[str, Any]]:
    summaries = report.get("summaries")
    if not isinstance(summaries, list) or not summaries:
        raise ValueError("Benchmark report has no summaries.")
    result: dict[int, Mapping[str, Any]] = {}
    for summary in summaries:
        if not isinstance(summary, Mapping):
            raise ValueError("Benchmark summary is invalid.")
        try:
            chunk_size = int(summary["fact_chunk_size"])
            float(summary["median_elapsed_seconds"])
            float(summary["median_fact_load_peak_rss_bytes"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Benchmark summary is incomplete.") from exc
        if chunk_size in result:
            raise ValueError("Benchmark report has duplicate chunk sizes.")
        result[chunk_size] = summary
    return result


def _percent_change(current: object, previous: object) -> float:
    old_value = float(previous)
    if old_value <= 0:
        raise ValueError("Baseline metrics must be positive.")
    return round((float(current) - old_value) / old_value * 100, 3)
