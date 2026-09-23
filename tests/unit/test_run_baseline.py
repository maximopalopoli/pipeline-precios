from datetime import datetime, timezone
from decimal import Decimal

from etl.run_baseline import load_run_baseline


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


def test_load_run_baseline_calculates_staged_throughput_from_durable_counts():
    conn = _Connection((
        7, True, datetime(2026, 9, 22, 12, tzinfo=timezone.utc),
        datetime(2026, 9, 22, 12, 0, 4, tzinfo=timezone.utc), Decimal("4.0"),
        321_841_103, 50_000, 1_048_533, 10_000, 5_000, 4_900, 100, 4_900, 0,
    ))

    baseline = load_run_baseline(conn, run_id=7)

    assert baseline is not None
    assert baseline.run_id == 7
    assert baseline.is_partial is True
    assert baseline.rows_productos_source == 1_048_533
    assert baseline.fact_chunk_size == 50_000
    assert baseline.rows_productos_raw == 5_000
    assert baseline.staged_rows_per_second == 1250.0
    assert conn.cursor_instance.calls[0][1] == (7,)
