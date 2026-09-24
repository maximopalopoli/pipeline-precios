"""Print comparable completed ETL replays for a selected ingestion run.

Usage:
    python scripts/compare_ingestion_baselines.py RUN_ID
"""

from __future__ import annotations

import json
import sys

import psycopg2

from etl.run_baseline import load_comparable_run_baselines
from etl.run_pipeline import _db_connect


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        print("usage: compare_ingestion_baselines.py RUN_ID", file=sys.stderr)
        return 2
    run_id = int(args[0])
    conn = _db_connect()
    try:
        try:
            baselines = load_comparable_run_baselines(conn, run_id=run_id)
        except psycopg2.errors.UndefinedColumn:
            print(
                "Database schema is outdated; run `make db` before comparing baselines.",
                file=sys.stderr,
            )
            return 1
    finally:
        conn.close()
    if not baselines:
        print(f"No completed comparable baselines found for run {run_id}.", file=sys.stderr)
        return 1
    print(json.dumps([baseline.as_dict() for baseline in baselines], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
