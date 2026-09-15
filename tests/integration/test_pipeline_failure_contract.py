"""Contract tests for terminal failures in the CLI ingestion entrypoint."""

from datetime import date
import sys

import psycopg
import pytest

import etl.run_pipeline as pipeline
from etl.extract.downloader import DownloadResult
from etl.ingestion_lock import acquire_ingestion_lock
from etl.quality import checks as quality_checks


_RUN_DATE = date(2026, 3, 15)


def _connect_to_fixture(pg_conn):
    return psycopg.connect(
        host=pg_conn.info.host,
        port=pg_conn.info.port,
        user=pg_conn.info.user,
        dbname=pg_conn.info.dbname,
    )


def _create_raw_file(conn) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO raw_files (filename, url, sha256, size_bytes, fecha_datos)
            VALUES ('quality-failure.zip', 'http://example.test/data.zip',
                    'quality-failure-contract', 1, %s)
            RETURNING id
            """,
            (_RUN_DATE,),
        )
        return cur.fetchone()[0]


def test_cli_propagates_critical_quality_failure_and_marks_run_failed(
    pg_conn, sample_zip_path, tmp_path, monkeypatch
):
    """A critical validation failure must be visible to schedulers and operators."""
    raw_file_id = _create_raw_file(pg_conn)
    pg_conn.commit()

    monkeypatch.setattr(pipeline, "_db_connect", lambda: _connect_to_fixture(pg_conn))
    monkeypatch.setattr(
        pipeline.downloader,
        "download",
        lambda conn, raw_dir, fecha: DownloadResult(
            raw_file_id=raw_file_id,
            filename="quality-failure.zip",
            path=sample_zip_path,
            sha256="quality-failure-contract",
            size_bytes=sample_zip_path.stat().st_size,
            fecha_datos=_RUN_DATE,
            is_new=True,
        ),
    )

    with pytest.raises(quality_checks.QualityCheckError):
        pipeline.run(fecha=_RUN_DATE, raw_dir=tmp_path)

    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT status, finished_at IS NOT NULL, error_message
            FROM ingestion_runs
            WHERE raw_file_id = %s
            """,
            (raw_file_id,),
        )
        status, finished, message = cur.fetchone()

    assert status == "failed"
    assert finished is True
    assert "min_row_count" in message
    with pg_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM ingestion_locks WHERE fecha_datos = %s", (_RUN_DATE,))
        assert cur.fetchone()[0] == 0


def test_cli_refuses_to_start_when_another_entrypoint_owns_the_date(
    pg_conn, sample_zip_path, tmp_path, monkeypatch
):
    raw_file_id = _create_raw_file(pg_conn)
    pg_conn.commit()
    acquire_ingestion_lock(
        pg_conn,
        fecha_datos=_RUN_DATE,
        raw_file_id=raw_file_id,
        owner="airflow:active-run",
    )
    monkeypatch.setattr(pipeline, "_db_connect", lambda: _connect_to_fixture(pg_conn))
    monkeypatch.setattr(
        pipeline.downloader,
        "download",
        lambda conn, raw_dir, fecha: DownloadResult(
            raw_file_id=raw_file_id,
            filename="quality-failure.zip",
            path=sample_zip_path,
            sha256="quality-failure-contract",
            size_bytes=sample_zip_path.stat().st_size,
            fecha_datos=_RUN_DATE,
            is_new=True,
        ),
    )

    with pytest.raises(RuntimeError, match="already locked by airflow:active-run"):
        pipeline.run(fecha=_RUN_DATE, raw_dir=tmp_path)

    with pg_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM ingestion_runs WHERE raw_file_id = %s", (raw_file_id,))
        assert cur.fetchone()[0] == 0


def test_main_exits_nonzero_when_the_pipeline_fails(monkeypatch):
    def fail_pipeline(**kwargs):
        raise RuntimeError("pipeline failed")

    monkeypatch.setattr(pipeline, "_configure_logging", lambda: None)
    monkeypatch.setattr(pipeline, "run", fail_pipeline)
    monkeypatch.setattr(sys, "argv", ["run_pipeline"])

    with pytest.raises(SystemExit) as exc_info:
        pipeline.main()

    assert exc_info.value.code == 1
