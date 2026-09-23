"""Fail only when a comparable ETL benchmark report regresses materially.

Usage:
    python scripts/check_benchmark_regression.py baseline.json candidate.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from etl.benchmark_regression import compare_benchmark_reports


def _load_report(path: Path) -> dict[str, object]:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read benchmark report {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Benchmark report {path} must be a JSON object.")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check comparable benchmark reports for material regressions."
    )
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--max-elapsed-regression-pct", type=float, default=15.0)
    parser.add_argument("--max-peak-rss-regression-pct", type=float, default=20.0)
    args = parser.parse_args(argv)
    try:
        results = compare_benchmark_reports(
            _load_report(args.baseline),
            _load_report(args.candidate),
            max_elapsed_regression_pct=args.max_elapsed_regression_pct,
            max_peak_rss_regression_pct=args.max_peak_rss_regression_pct,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(json.dumps([result.as_dict() for result in results], indent=2))
    if any(result.exceeds_elapsed_limit or result.exceeds_peak_rss_limit for result in results):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
