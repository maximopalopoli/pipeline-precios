"""Retention cleanup for completed and failed staging runs."""

from datetime import date

import structlog

from etl.staging_retention import find_expired_staging_runs, purge_expired_staging_runs
from etl.staging.loader import _ensure_productos_partition


def _create_run(conn, *, status: str, finished_at: str | None) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO raw_files (filename, url, sha256, size_bytes, fecha_datos)
            VALUES (%s, 'http://example.test/data.zip', %s, 1, '2026-03-15')
            RETURNING id
            """,
            (f"staging-{status}-{finished_at}.zip", f"staging-{status}-{finished_at}"),
        )
        raw_file_id = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO ingestion_runs (raw_file_id, status, finished_at)
            VALUES (%s, %s, %s)
            RETURNING id
            """,
            (raw_file_id, status, finished_at),
        )
        return cur.fetchone()[0]


def _add_staging_rows(conn, run_id: int) -> None:
    _ensure_productos_partition(conn, run_id, structlog.get_logger())
    with conn.cursor() as cur:
        cur.execute("INSERT INTO stg_comercios (run_id) VALUES (%s)", (run_id,))
        cur.execute("INSERT INTO stg_sucursales (run_id) VALUES (%s)", (run_id,))
        cur.execute("INSERT INTO stg_productos (run_id) VALUES (%s)", (run_id,))
    conn.commit()


def test_staging_retention_excludes_running_and_recent_runs(pg_conn):
    expired = _create_run(pg_conn, status="completed", finished_at="2026-01-01")
    _create_run(pg_conn, status="failed", finished_at="2026-03-14")
    _create_run(pg_conn, status="running", finished_at=None)
    pg_conn.commit()

    assert find_expired_staging_runs(pg_conn, cutoff=date(2026, 3, 1)) == (expired,)


def test_explicit_staging_purge_removes_only_expired_terminal_run_data(pg_conn):
    expired = _create_run(pg_conn, status="completed", finished_at="2026-01-01")
    recent = _create_run(pg_conn, status="failed", finished_at="2026-03-14")
    _add_staging_rows(pg_conn, expired)
    _add_staging_rows(pg_conn, recent)

    result = purge_expired_staging_runs(pg_conn, cutoff=date(2026, 3, 1), dry_run=False)
    pg_conn.commit()

    assert result.run_ids == (expired,)
    assert result.rows_comercios == 1
    assert result.rows_sucursales == 1
    assert result.dropped_product_partitions == 1
    with pg_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM stg_comercios WHERE run_id = %s", (expired,))
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT COUNT(*) FROM stg_comercios WHERE run_id = %s", (recent,))
        assert cur.fetchone()[0] == 1
        cur.execute("SELECT to_regclass(%s)", (f"public.stg_productos_{expired}",))
        assert cur.fetchone()[0] is None
        cur.execute("SELECT to_regclass(%s)", (f"public.stg_productos_{recent}",))
        assert cur.fetchone()[0] == f"stg_productos_{recent}"
