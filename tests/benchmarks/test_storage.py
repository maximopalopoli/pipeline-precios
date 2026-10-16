"""Storage-footprint telemetry stays cheap and handles absent partitions."""

from benchmarks.storage import StorageFootprint, collect_storage_footprint


class _Cursor:
    def __init__(self):
        self.parameters = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, parameters):
        assert "pg_database_size" in sql
        assert "to_regclass" in sql
        self.parameters = parameters

    def fetchone(self):
        return (1000, 200, 30, 40, 50, 60)


class _Connection:
    def __init__(self):
        self.cursor_instance = _Cursor()

    def cursor(self):
        return self.cursor_instance


def test_storage_footprint_reports_database_and_run_partition_sizes():
    conn = _Connection()

    footprint = collect_storage_footprint(conn, run_id=42)

    assert footprint == StorageFootprint(1000, 200, 30, 40, 50, 60)
    assert conn.cursor_instance.parameters == (42,)
