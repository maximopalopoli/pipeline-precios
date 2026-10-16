"""Contract that decides whether a downloaded raw file is ingested.

The pipeline decides whether a downloaded raw file may be processed from
``ingestion_runs``.  A file is skipped only after at least one successful
publication.  Failed or interrupted attempts remain eligible for a retry.
"""

from etl.ingestion_run import should_process_raw_file


def _create_raw_file(conn, sha256: str, fecha_datos: str = "2026-03-15") -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO raw_files (filename, url, sha256, size_bytes, fecha_datos)
            VALUES (%s, 'http://example.test/data.zip', %s, 1, %s)
            RETURNING id
            """,
            (f"{sha256}.zip", sha256, fecha_datos),
        )
        return cur.fetchone()[0]


def _create_run(
    conn,
    raw_file_id: int,
    status: str,
    *,
    is_partial: bool = False,
    regional_scope: str | None = None,
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO ingestion_runs (raw_file_id, status, is_partial, regional_scope)
            VALUES (%s, %s, %s, %s) RETURNING id
            """,
            (raw_file_id, status, is_partial, regional_scope),
        )
        return cur.fetchone()[0]


def test_new_raw_file_is_eligible(pg_conn):
    raw_file_id = _create_raw_file(pg_conn, "entrypoint-new")
    pg_conn.commit()

    assert should_process_raw_file(pg_conn, raw_file_id) is True


def test_failed_raw_file_remains_eligible_for_retry(pg_conn):
    raw_file_id = _create_raw_file(pg_conn, "entrypoint-failed")
    _create_run(pg_conn, raw_file_id, "failed")
    pg_conn.commit()

    assert should_process_raw_file(pg_conn, raw_file_id) is True


def test_running_raw_file_is_not_treated_as_a_completed_ingestion(pg_conn):
    raw_file_id = _create_raw_file(pg_conn, "entrypoint-running")
    _create_run(pg_conn, raw_file_id, "running")
    pg_conn.commit()

    assert should_process_raw_file(pg_conn, raw_file_id) is True


def test_completed_raw_file_is_the_only_case_skipped(pg_conn):
    raw_file_id = _create_raw_file(pg_conn, "entrypoint-completed")
    _create_run(pg_conn, raw_file_id, "failed")
    _create_run(pg_conn, raw_file_id, "completed")
    pg_conn.commit()

    assert should_process_raw_file(pg_conn, raw_file_id) is False


def test_completed_partial_run_never_blocks_a_full_ingestion(pg_conn):
    raw_file_id = _create_raw_file(pg_conn, "entrypoint-partial")
    _create_run(pg_conn, raw_file_id, "completed", is_partial=True)
    pg_conn.commit()

    assert should_process_raw_file(pg_conn, raw_file_id) is True


def test_partial_ingestion_can_inspect_a_source_already_published_fully(pg_conn):
    raw_file_id = _create_raw_file(pg_conn, "entrypoint-full-then-partial")
    _create_run(pg_conn, raw_file_id, "completed")
    pg_conn.commit()

    assert should_process_raw_file(pg_conn, raw_file_id, partial=True) is True


def test_a_scope_change_makes_a_completed_file_eligible_again(pg_conn):
    raw_file_id = _create_raw_file(pg_conn, "entrypoint-scope-change")
    _create_run(pg_conn, raw_file_id, "completed")
    pg_conn.commit()

    assert should_process_raw_file(pg_conn, raw_file_id, regional_scope="amba") is True


def test_a_file_completed_with_the_same_scope_is_skipped(pg_conn):
    raw_file_id = _create_raw_file(pg_conn, "entrypoint-same-scope")
    _create_run(pg_conn, raw_file_id, "completed", regional_scope="amba")
    pg_conn.commit()

    assert should_process_raw_file(pg_conn, raw_file_id, regional_scope="amba") is False
    assert should_process_raw_file(pg_conn, raw_file_id) is True


def test_a_rectification_of_a_published_day_is_processed(pg_conn):
    # The 06:30 run published the day; the 10:30 run downloads a rectified
    # file with another hash for the same date and must ingest it.
    first_id = _create_raw_file(pg_conn, "entrypoint-0630", "2026-03-17")
    _create_run(pg_conn, first_id, "completed")
    rectified_id = _create_raw_file(pg_conn, "entrypoint-1030", "2026-03-17")
    pg_conn.commit()

    assert should_process_raw_file(pg_conn, first_id) is False
    assert should_process_raw_file(pg_conn, rectified_id) is True


def test_a_late_delivery_serving_last_weeks_published_file_is_skipped(pg_conn):
    # Before a chain delivers, the weekday URL still serves last week's ZIP.
    # Its hash is already published, so the run stops before staging and the
    # 10:30 run ingests the day once the new file arrives.
    stale_id = _create_raw_file(pg_conn, "entrypoint-last-week", "2026-03-10")
    _create_run(pg_conn, stale_id, "completed")
    pg_conn.commit()

    assert should_process_raw_file(pg_conn, stale_id) is False
