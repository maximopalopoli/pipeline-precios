"""Retention of immutable local raw ZIPs after successful ingestion."""

import hashlib
from datetime import date

from etl.raw_archive import archive_completed_raw_files
from etl.raw_retention import find_expired_raw_files, purge_expired_raw_files


def _create_raw_file(conn, *, suffix: str, status: str, archived: bool = True) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO raw_files (
                filename, url, sha256, size_bytes, fecha_datos, archive_key, archived_at
            )
            VALUES (
                %s, 'http://example.test/data.zip', %s, 1, '2026-01-01',
                %s, CASE WHEN %s THEN NOW() END
            )
            RETURNING id
            """,
            (
                f"2026-01-01_lunes_{suffix}.zip",
                suffix,
                f"2026/01/2026-01-01_{suffix}.zip" if archived else None,
                archived,
            ),
        )
        raw_file_id = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO ingestion_runs (raw_file_id, status, finished_at)
            VALUES (%s, %s, CASE WHEN %s = 'running' THEN NULL ELSE NOW() END)
            """,
            (raw_file_id, status, status),
        )
    conn.commit()
    return raw_file_id


def _filename(conn, raw_file_id: int) -> str:
    with conn.cursor() as cur:
        cur.execute("SELECT filename FROM raw_files WHERE id = %s", (raw_file_id,))
        return cur.fetchone()[0]


def _write_local_and_archived(conn, raw_file_id: int, raw_dir, archive_dir):
    """Write the local ZIP and its archived copy with the registered size."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT filename, archive_key FROM raw_files WHERE id = %s", (raw_file_id,)
        )
        filename, archive_key = cur.fetchone()
    local = raw_dir / filename
    local.write_bytes(b"z")
    if archive_key is not None:
        archived = archive_dir / archive_key
        archived.parent.mkdir(parents=True, exist_ok=True)
        archived.write_bytes(b"z")
    return local


def test_raw_retention_excludes_unresolved_runs(pg_conn, tmp_path):
    completed = _create_raw_file(pg_conn, suffix="completed", status="completed")
    failed = _create_raw_file(pg_conn, suffix="failed", status="failed")
    running = _create_raw_file(pg_conn, suffix="running", status="running")
    for raw_file_id in (completed, failed, running):
        (tmp_path / _filename(pg_conn, raw_file_id)).write_bytes(b"zip")

    eligible = find_expired_raw_files(pg_conn, cutoff=date(2026, 2, 1))

    assert tuple(raw_file.id for raw_file in eligible) == (completed,)


def test_raw_retention_accepts_a_file_recovered_after_a_failed_run(pg_conn):
    raw_file_id = _create_raw_file(pg_conn, suffix="recovered", status="failed")
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO ingestion_runs (raw_file_id, status, finished_at)
            VALUES (%s, 'completed', NOW())
            """,
            (raw_file_id,),
        )
    pg_conn.commit()

    eligible = find_expired_raw_files(pg_conn, cutoff=date(2026, 2, 1))

    assert tuple(raw_file.id for raw_file in eligible) == (raw_file_id,)


def test_explicit_raw_purge_removes_only_eligible_local_zip_and_audits_it(
    pg_conn, tmp_path
):
    archive_dir = tmp_path / "archive"
    completed = _create_raw_file(pg_conn, suffix="completed-delete", status="completed")
    failed = _create_raw_file(pg_conn, suffix="failed-keep", status="failed")
    completed_path = _write_local_and_archived(
        pg_conn, completed, tmp_path, archive_dir
    )
    failed_path = _write_local_and_archived(pg_conn, failed, tmp_path, archive_dir)

    result = purge_expired_raw_files(
        pg_conn,
        raw_dir=tmp_path,
        archive_dir=archive_dir,
        cutoff=date(2026, 2, 1),
        dry_run=False,
    )
    pg_conn.commit()

    assert result.deleted_file_ids == (completed,)
    assert completed_path.exists() is False
    assert failed_path.exists() is True
    with pg_conn.cursor() as cur:
        cur.execute("SELECT kind, cutoff, targets FROM retention_events")
        assert cur.fetchone() == ("raw_files", date(2026, 2, 1), [completed])


def test_an_unarchived_zip_is_never_eligible(pg_conn):
    archived = _create_raw_file(pg_conn, suffix="archived", status="completed")
    _create_raw_file(pg_conn, suffix="not-archived", status="completed", archived=False)

    eligible = find_expired_raw_files(pg_conn, cutoff=date(2026, 2, 1))

    assert tuple(raw_file.id for raw_file in eligible) == (archived,)


def test_a_zip_whose_archived_copy_is_missing_is_kept(pg_conn, tmp_path):
    archive_dir = tmp_path / "archive"
    lost = _create_raw_file(pg_conn, suffix="archive-lost", status="completed")
    truncated = _create_raw_file(
        pg_conn, suffix="archive-truncated", status="completed"
    )
    lost_path = _write_local_and_archived(pg_conn, lost, tmp_path, archive_dir)
    truncated_path = _write_local_and_archived(
        pg_conn, truncated, tmp_path, archive_dir
    )
    (archive_dir / "2026/01/2026-01-01_archive-lost.zip").unlink()
    (archive_dir / "2026/01/2026-01-01_archive-truncated.zip").write_bytes(b"")

    result = purge_expired_raw_files(
        pg_conn,
        raw_dir=tmp_path,
        archive_dir=archive_dir,
        cutoff=date(2026, 2, 1),
        dry_run=False,
    )
    pg_conn.commit()

    assert result.deleted_file_ids == ()
    assert result.archive_missing_file_ids == (lost, truncated)
    assert lost_path.exists() and truncated_path.exists()
    with pg_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM retention_events")
        assert cur.fetchone()[0] == 0


def test_retention_after_archiving_leaves_only_the_archived_copy(pg_conn, tmp_path):
    raw_dir = tmp_path / "raw"
    archive_dir = tmp_path / "archive"
    raw_dir.mkdir()
    raw_file_id = _create_raw_file(
        pg_conn, suffix="end-to-end", status="completed", archived=False
    )
    local = raw_dir / _filename(pg_conn, raw_file_id)
    local.write_bytes(b"z")
    with pg_conn.cursor() as cur:
        cur.execute(
            "UPDATE raw_files SET sha256 = %s WHERE id = %s",
            (hashlib.sha256(b"z").hexdigest(), raw_file_id),
        )
    pg_conn.commit()

    archive_completed_raw_files(pg_conn, raw_dir=raw_dir, archive_dir=archive_dir)
    result = purge_expired_raw_files(
        pg_conn,
        raw_dir=raw_dir,
        archive_dir=archive_dir,
        cutoff=date(2026, 2, 1),
        dry_run=False,
    )
    pg_conn.commit()

    assert result.deleted_file_ids == (raw_file_id,)
    assert local.exists() is False
    assert [path.read_bytes() for path in archive_dir.rglob("*.zip")] == [b"z"]
