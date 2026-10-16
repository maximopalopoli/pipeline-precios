import pytest

from benchmarks.regression import compare_benchmark_reports


def _report(*, elapsed: float = 10, rss: int = 100, fecha: str = "2026-09-22"):
    return {
        "source_archive": "/tmp/sepa.zip",
        "fecha": fecha,
        "policy": {"chain_keys": ["12:1"], "max_product_rows": 50_000},
        "summaries": [{
            "fact_chunk_size": 5_000,
            "median_elapsed_seconds": elapsed,
            "median_fact_load_peak_rss_bytes": rss,
        }],
    }


def test_compare_benchmark_reports_marks_only_material_regressions():
    comparisons = compare_benchmark_reports(
        _report(), _report(elapsed=11, rss=125),
        max_elapsed_regression_pct=15,
        max_peak_rss_regression_pct=20,
    )

    assert comparisons[0].elapsed_regression_pct == 10.0
    assert comparisons[0].peak_rss_regression_pct == 25.0
    assert comparisons[0].exceeds_elapsed_limit is False
    assert comparisons[0].exceeds_peak_rss_limit is True


def test_compare_benchmark_reports_requires_identical_benchmark_scope():
    with pytest.raises(ValueError, match="fecha"):
        compare_benchmark_reports(
            _report(), _report(fecha="2026-09-23"),
            max_elapsed_regression_pct=15,
            max_peak_rss_regression_pct=20,
        )
