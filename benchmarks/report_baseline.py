"""Print ETL baseline data for the latest or a selected run.

The storage footprint is measured now, not when the run finished: report the
latest run right after it to keep the two comparable.
"""

from __future__ import annotations

import json
import sys

import psycopg2

from benchmarks.run_baseline import latest_completed_run_id, load_run_baseline
from benchmarks.storage import collect_storage_footprint
from etl.run_pipeline import _db_connect


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) > 1:
        print("usage: python -m benchmarks.report_baseline [run_id]", file=sys.stderr)
        return 2
    requested_run_id = int(args[0]) if args else None
    conn = _db_connect()
    try:
        run_id = requested_run_id or latest_completed_run_id(conn)
        if run_id is None:
            print("No completed ingestion runs found.", file=sys.stderr)
            return 1
        try:
            baseline = load_run_baseline(conn, run_id=run_id)
            storage = collect_storage_footprint(conn, run_id=run_id)
        except psycopg2.errors.UndefinedColumn:
            print(
                "Database schema is outdated; run `make db` before reporting baselines.",
                file=sys.stderr,
            )
            return 1
    finally:
        conn.close()
    if baseline is None:
        print(f"Completed ingestion run {run_id} not found.", file=sys.stderr)
        return 1
    report = {**baseline.as_dict(), "storage": storage.__dict__}
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
