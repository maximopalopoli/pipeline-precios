"""Audit representative warehouse query plans with PostgreSQL evidence.

Usage:
    python -m benchmarks.audit_queries [fecha_id]
"""

from __future__ import annotations

import json
import sys

from benchmarks.query_audit import audit_queries, representative_queries
from etl.run_pipeline import _db_connect


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) > 1:
        print("usage: python -m benchmarks.audit_queries [fecha_id]", file=sys.stderr)
        return 2
    fecha_id = int(args[0]) if args else None
    conn = _db_connect()
    try:
        summaries = audit_queries(
            conn, representative_queries(conn, fecha_id=fecha_id)
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        conn.close()
    print(json.dumps([summary.as_dict() for summary in summaries], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
