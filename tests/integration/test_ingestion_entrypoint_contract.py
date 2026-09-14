"""Contract shared by the CLI and Airflow ingestion entry points.

Both entry points decide whether a downloaded raw file may be processed from
``ingestion_runs``.  A file is skipped only after at least one successful
publication.  Failed or interrupted attempts remain eligible for a retry.
"""

from etl.run_pipeline import _has_completed_run


def _create_raw_file(conn, sha256: str) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO raw_files (filename, url, sha256, size_bytes, fecha_datos)
            VALUES (%s, 'http://example.test/data.zip', %s, 1, '2026-03-15')
            RETURNING id
            """,
            (f"{sha256}.zip", sha256),
        )
        return cur.fetchone()[0]


def _create_run(conn, raw_file_id: int, status: str) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ingestion_runs (raw_file_id, status) VALUES (%s, %s) RETURNING id",
            (raw_file_id, status),
        )
        return cur.fetchone()[0]


def test_new_raw_file_is_eligible_for_both_entrypoints(pg_conn):
    raw_file_id = _create_raw_file(pg_conn, "entrypoint-new")
    pg_conn.commit()

    assert _has_completed_run(pg_conn, raw_file_id) is False


def test_failed_raw_file_remains_eligible_for_retry(pg_conn):
    raw_file_id = _create_raw_file(pg_conn, "entrypoint-failed")
    _create_run(pg_conn, raw_file_id, "failed")
    pg_conn.commit()

    assert _has_completed_run(pg_conn, raw_file_id) is False


def test_running_raw_file_is_not_treated_as_a_completed_ingestion(pg_conn):
    raw_file_id = _create_raw_file(pg_conn, "entrypoint-running")
    _create_run(pg_conn, raw_file_id, "running")
    pg_conn.commit()

    assert _has_completed_run(pg_conn, raw_file_id) is False


def test_completed_raw_file_is_the_only_case_both_entrypoints_skip(pg_conn):
    raw_file_id = _create_raw_file(pg_conn, "entrypoint-completed")
    _create_run(pg_conn, raw_file_id, "failed")
    _create_run(pg_conn, raw_file_id, "completed")
    pg_conn.commit()

    assert _has_completed_run(pg_conn, raw_file_id) is True
