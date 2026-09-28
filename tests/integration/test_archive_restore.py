"""Re-ingesting a day from its archived source ZIP."""

import hashlib
import shutil
from datetime import date

import psycopg
import pytest

import etl.run_pipeline as cli_pipeline
from etl.raw_archive import archive_key_for, find_archived_zip, register_archived_zip

_RUN_DATE = date(2026, 3, 17)


def _connect_to_fixture(pg_conn):
    return psycopg.connect(
        host=pg_conn.info.host,
        port=pg_conn.info.port,
        user=pg_conn.info.user,
        dbname=pg_conn.info.dbname,
    )


def _archive(archive_dir, payload: bytes, fecha: date = _RUN_DATE):
    key = archive_key_for(fecha, hashlib.sha256(payload).hexdigest())
    path = archive_dir / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


@pytest.fixture()
def cli(pg_conn, monkeypatch):
    original_run_all = cli_pipeline.quality_checks.run_all
    monkeypatch.delenv("SEPA_ARCHIVE_DIR", raising=False)
    monkeypatch.setattr(
        cli_pipeline, "_db_connect", lambda: _connect_to_fixture(pg_conn)
    )
    monkeypatch.setattr(
        cli_pipeline.quality_checks,
        "run_all",
        lambda conn, run_id: original_run_all(conn, run_id, min_productos=1),
    )
    return cli_pipeline


def test_a_day_removed_locally_is_reingested_from_the_archive(
    pg_conn, sample_zip_path, tmp_path, cli
):
    local_zip = tmp_path / "sepa_martes.zip"
    shutil.copyfile(sample_zip_path, local_zip)
    first = cli.run(fecha=_RUN_DATE, raw_dir=tmp_path, source_archive=local_zip)
    assert first.rows_loaded == 7
    local_zip.unlink()
    with pg_conn.cursor() as cur:
        cur.execute("DELETE FROM fact_precios WHERE fecha_id = 20260317")
    pg_conn.commit()

    restored = cli.run(fecha=_RUN_DATE, raw_dir=tmp_path, from_archive=True)

    assert restored.rows_loaded == 7
    with pg_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM fact_precios WHERE fecha_id = 20260317")
        assert cur.fetchone()[0] == 7
        cur.execute(
            "SELECT COUNT(*) FROM ingestion_runs WHERE status = 'completed' AND NOT is_partial"
        )
        assert cur.fetchone()[0] == 2
        cur.execute("SELECT COUNT(*) FROM raw_files")
        assert cur.fetchone()[0] == 1


def test_a_rebuilt_database_finds_the_zip_in_the_archive_folder(
    pg_conn, sample_zip_path, tmp_path, cli
):
    archived = _archive(tmp_path / "archive", sample_zip_path.read_bytes())

    result = cli.run(fecha=_RUN_DATE, raw_dir=tmp_path, from_archive=True)

    assert result.rows_loaded == 7
    with pg_conn.cursor() as cur:
        cur.execute("SELECT archive_key, archived_at IS NOT NULL FROM raw_files")
        assert cur.fetchone() == (
            archived.relative_to(tmp_path / "archive").as_posix(),
            True,
        )


def test_several_archived_versions_without_registry_are_ambiguous(pg_conn, tmp_path):
    _archive(tmp_path, b"early")
    _archive(tmp_path, b"rectified")

    with pytest.raises(ValueError, match="Several archived source ZIPs"):
        find_archived_zip(pg_conn, archive_dir=tmp_path, fecha=_RUN_DATE)


def test_a_missing_archive_is_reported(pg_conn, tmp_path):
    with pytest.raises(FileNotFoundError, match="No archived source ZIP"):
        find_archived_zip(pg_conn, archive_dir=tmp_path, fecha=_RUN_DATE)


def test_a_registered_archive_whose_copy_disappeared_is_reported(pg_conn, tmp_path):
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO raw_files (
                filename, url, sha256, size_bytes, fecha_datos, archive_key, archived_at
            )
            VALUES ('x.zip', 'http://example.test/x.zip', 'abc', 1, %s,
                    '2026/03/2026-03-17_abc.zip', NOW())
            """,
            (_RUN_DATE,),
        )
    pg_conn.commit()

    with pytest.raises(FileNotFoundError, match="Archived copy is missing"):
        find_archived_zip(pg_conn, archive_dir=tmp_path, fecha=_RUN_DATE)


def test_the_registry_prefers_the_latest_full_publication(pg_conn, tmp_path):
    early = _archive(tmp_path, b"early")
    rectified = _archive(tmp_path, b"rectified")
    with pg_conn.cursor() as cur:
        for path, finished_at in (
            (rectified, "2026-03-17 10:30"),
            (early, "2026-03-17 06:30"),
        ):
            sha256 = path.stem.split("_", 1)[1]
            cur.execute(
                """
                INSERT INTO raw_files (
                    filename, url, sha256, size_bytes, fecha_datos,
                    archive_key, archived_at
                )
                VALUES (%s, 'http://example.test/x.zip', %s, 1, %s, %s, NOW())
                RETURNING id
                """,
                (path.name, sha256, _RUN_DATE, path.relative_to(tmp_path).as_posix()),
            )
            cur.execute(
                """
                INSERT INTO ingestion_runs (raw_file_id, status, finished_at)
                VALUES (%s, 'completed', %s)
                """,
                (cur.fetchone()[0], finished_at),
            )
    pg_conn.commit()

    assert (
        find_archived_zip(pg_conn, archive_dir=tmp_path, fecha=_RUN_DATE) == rectified
    )


def test_a_copy_that_does_not_match_its_hash_is_rejected(pg_conn, tmp_path):
    path = _archive(tmp_path, b"original")
    path.write_bytes(b"tampered")

    with pytest.raises(ValueError, match="does not match its hash"):
        register_archived_zip(pg_conn, archive_dir=tmp_path, fecha=_RUN_DATE)
    with pg_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM raw_files")
        assert cur.fetchone()[0] == 0
