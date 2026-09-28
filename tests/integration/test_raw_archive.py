"""Archive status and local archiving of completed raw ZIPs."""

import hashlib
from datetime import date
from pathlib import Path

import psycopg
import pytest

from etl.raw_archive import (
    RawArchiveResult,
    archive_completed_raw_files,
    archive_dir_from_environment,
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


def _write_registered_zip(conn, raw_dir, *, payload: bytes, fecha: str) -> int:
    sha256 = hashlib.sha256(payload).hexdigest()
    filename = f"{fecha}_{sha256[:8]}.zip"
    (raw_dir / filename).write_bytes(payload)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO raw_files (filename, url, sha256, size_bytes, fecha_datos)
            VALUES (%s, 'http://example.test/data.zip', %s, %s, %s)
            RETURNING id
            """,
            (filename, sha256, len(payload), fecha),
        )
        raw_file_id = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO ingestion_runs (raw_file_id, status, finished_at)
            VALUES (%s, 'completed', NOW())
            """,
            (raw_file_id,),
        )
    conn.commit()
    return raw_file_id


def test_completed_zips_are_archived_by_declared_date_and_hash(pg_conn, tmp_path):
    raw_dir = tmp_path / "raw"
    archive_dir = tmp_path / "archive"
    raw_dir.mkdir()
    payload = b"sepa zip"
    raw_file_id = _write_registered_zip(
        pg_conn, raw_dir, payload=payload, fecha="2026-09-25"
    )

    result = archive_completed_raw_files(
        pg_conn, raw_dir=raw_dir, archive_dir=archive_dir
    )

    key = archive_key_for(date(2026, 9, 25), hashlib.sha256(payload).hexdigest())
    assert result == RawArchiveResult((raw_file_id,), (), ())
    assert (archive_dir / key).read_bytes() == payload
    assert _archive_status(pg_conn, raw_file_id) == (key, True)
    assert list(archive_dir.rglob("*.part")) == []


def test_archiving_survives_removal_of_the_local_zip(pg_conn, tmp_path):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    raw_file_id = _write_registered_zip(
        pg_conn, raw_dir, payload=b"linked", fecha="2026-09-25"
    )

    archive_completed_raw_files(
        pg_conn, raw_dir=raw_dir, archive_dir=tmp_path / "archive"
    )
    for path in raw_dir.glob("*.zip"):
        path.unlink()

    key = _archive_status(pg_conn, raw_file_id)[0]
    assert (tmp_path / "archive" / key).read_bytes() == b"linked"


def test_a_corrupted_local_zip_is_not_archived(pg_conn, tmp_path):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    corrupted = _write_registered_zip(
        pg_conn, raw_dir, payload=b"original", fecha="2026-09-24"
    )
    healthy = _write_registered_zip(
        pg_conn, raw_dir, payload=b"healthy", fecha="2026-09-25"
    )
    (raw_dir / _filename(pg_conn, corrupted)).write_bytes(b"tampered")

    result = archive_completed_raw_files(
        pg_conn, raw_dir=raw_dir, archive_dir=tmp_path / "archive"
    )

    assert result == RawArchiveResult((healthy,), (), (corrupted,))
    assert _archive_status(pg_conn, corrupted) == (None, False)
    assert len(list((tmp_path / "archive").rglob("*.zip"))) == 1
    assert list((tmp_path / "archive").rglob("*.part")) == []


def test_a_missing_local_zip_is_reported_and_left_pending(pg_conn, tmp_path):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    raw_file_id = _write_registered_zip(
        pg_conn, raw_dir, payload=b"gone", fecha="2026-09-25"
    )
    (raw_dir / _filename(pg_conn, raw_file_id)).unlink()

    result = archive_completed_raw_files(
        pg_conn, raw_dir=raw_dir, archive_dir=tmp_path / "archive"
    )

    assert result == RawArchiveResult((), (raw_file_id,), ())
    assert [raw.id for raw in find_unarchived_raw_files(pg_conn)] == [raw_file_id]


def test_an_existing_verified_copy_is_reused(pg_conn, tmp_path):
    raw_dir = tmp_path / "raw"
    archive_dir = tmp_path / "archive"
    raw_dir.mkdir()
    payload = b"already there"
    raw_file_id = _write_registered_zip(
        pg_conn, raw_dir, payload=payload, fecha="2026-09-25"
    )
    key = archive_key_for(date(2026, 9, 25), hashlib.sha256(payload).hexdigest())
    (archive_dir / key).parent.mkdir(parents=True)
    (archive_dir / key).write_bytes(payload)

    result = archive_completed_raw_files(
        pg_conn, raw_dir=raw_dir, archive_dir=archive_dir
    )

    assert result.archived_file_ids == (raw_file_id,)
    assert (archive_dir / key).read_bytes() == payload


def test_archive_dir_defaults_inside_the_raw_dir(tmp_path):
    assert archive_dir_from_environment(tmp_path, {}) == tmp_path / "archive"
    assert archive_dir_from_environment(
        tmp_path, {"SEPA_ARCHIVE_DIR": "/mnt/backup/sepa"}
    ) == Path("/mnt/backup/sepa")


def _filename(conn, raw_file_id: int) -> str:
    with conn.cursor() as cur:
        cur.execute("SELECT filename FROM raw_files WHERE id = %s", (raw_file_id,))
        return cur.fetchone()[0]
