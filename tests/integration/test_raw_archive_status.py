"""Archive status recorded for completed raw ZIPs."""

from datetime import date

import psycopg
import pytest

from etl.raw_archive import (
    archive_key_for,
    find_unarchived_raw_files,
    mark_raw_file_archived,
)


def _create_raw_file(conn, *, sha256: str, fecha: str, status: str | None) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO raw_files (filename, url, sha256, size_bytes, fecha_datos)
            VALUES (%s, 'http://example.test/data.zip', %s, 10, %s)
            RETURNING id
            """,
            (f"{sha256}.zip", sha256, fecha),
        )
        raw_file_id = cur.fetchone()[0]
        if status is not None:
            cur.execute(
                """
                INSERT INTO ingestion_runs (raw_file_id, status, finished_at)
                VALUES (%s, %s, CASE WHEN %s = 'running' THEN NULL ELSE NOW() END)
                """,
                (raw_file_id, status, status),
            )
    conn.commit()
    return raw_file_id


def _archive_status(conn, raw_file_id: int):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT archive_key, archived_at IS NOT NULL FROM raw_files WHERE id = %s",
            (raw_file_id,),
        )
        return cur.fetchone()


def test_archive_key_uses_declared_date_and_hash():
    assert archive_key_for(date(2026, 9, 25), "abc") == "2026/09/2026-09-25_abc.zip"


def test_only_completed_unarchived_files_are_candidates(pg_conn):
    later = _create_raw_file(
        pg_conn, sha256="later", fecha="2026-01-02", status="completed"
    )
    earlier = _create_raw_file(
        pg_conn, sha256="earlier", fecha="2026-01-01", status="completed"
    )
    _create_raw_file(pg_conn, sha256="failed", fecha="2026-01-01", status="failed")
    _create_raw_file(pg_conn, sha256="running", fecha="2026-01-01", status="running")
    _create_raw_file(pg_conn, sha256="never-run", fecha="2026-01-01", status=None)

    candidates = find_unarchived_raw_files(pg_conn)

    assert [raw.id for raw in candidates] == [earlier, later]
    assert candidates[0].sha256 == "earlier"
    assert candidates[0].size_bytes == 10
    assert candidates[0].fecha_datos == date(2026, 1, 1)


def test_marking_a_file_archived_removes_it_from_candidates(pg_conn):
    raw_file_id = _create_raw_file(
        pg_conn, sha256="done", fecha="2026-01-01", status="completed"
    )
    key = archive_key_for(date(2026, 1, 1), "done")

    mark_raw_file_archived(pg_conn, raw_file_id=raw_file_id, archive_key=key)
    pg_conn.commit()

    assert _archive_status(pg_conn, raw_file_id) == (key, True)
    assert find_unarchived_raw_files(pg_conn) == ()


def test_a_file_cannot_be_archived_twice(pg_conn):
    raw_file_id = _create_raw_file(
        pg_conn, sha256="twice", fecha="2026-01-01", status="completed"
    )
    mark_raw_file_archived(pg_conn, raw_file_id=raw_file_id, archive_key="first.zip")
    pg_conn.commit()

    with pytest.raises(ValueError, match="already archived"):
        mark_raw_file_archived(
            pg_conn, raw_file_id=raw_file_id, archive_key="second.zip"
        )
    pg_conn.rollback()

    assert _archive_status(pg_conn, raw_file_id) == ("first.zip", True)


def test_archive_key_and_timestamp_must_be_set_together(pg_conn):
    raw_file_id = _create_raw_file(
        pg_conn, sha256="partial", fecha="2026-01-01", status="completed"
    )

    with pytest.raises(psycopg.errors.CheckViolation):
        with pg_conn.cursor() as cur:
            cur.execute(
                "UPDATE raw_files SET archive_key = 'x.zip' WHERE id = %s",
                (raw_file_id,),
            )
    pg_conn.rollback()
