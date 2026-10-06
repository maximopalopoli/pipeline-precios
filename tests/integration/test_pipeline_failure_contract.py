"""Contract tests for terminal failures in the CLI ingestion entrypoint."""

from datetime import date
import sys

import psycopg
import pytest

import etl.run_pipeline as pipeline
from etl.extract.downloader import DownloadResult
from etl.ingestion_lock import acquire_ingestion_lock
from etl.ingestion_run import should_process_raw_file
from etl.quality import checks as quality_checks


_RUN_DATE = date(2026, 3, 17)


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


def _create_raw_file_for_date(conn, fecha_datos: date, sha256: str) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO raw_files (filename, url, sha256, size_bytes, fecha_datos)
            VALUES ('source-date-mismatch.zip', 'http://example.test/data.zip', %s, 1, %s)
            RETURNING id
            """,
            (sha256, fecha_datos),
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


def test_cli_marks_a_source_date_mismatch_failed_and_keeps_the_audit_dates(
    pg_conn, sample_zip_path, tmp_path, monkeypatch
):
    requested_date = date(2026, 3, 16)
    raw_file_id = _create_raw_file_for_date(
        pg_conn, requested_date, "source-date-mismatch-cli"
    )
    pg_conn.commit()

    monkeypatch.setattr(pipeline, "_db_connect", lambda: _connect_to_fixture(pg_conn))
    monkeypatch.setattr(
        pipeline.downloader,
        "download",
        lambda conn, raw_dir, fecha: DownloadResult(
            raw_file_id=raw_file_id,
            filename="source-date-mismatch.zip",
            path=sample_zip_path,
            sha256="source-date-mismatch-cli",
            size_bytes=sample_zip_path.stat().st_size,
            fecha_datos=requested_date,
            is_new=True,
        ),
    )

    with pytest.raises(ValueError, match="does not match logical ingestion date"):
        pipeline.run(fecha=requested_date, raw_dir=tmp_path)

    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT status, fecha_solicitada, fecha_logica, fecha_fuente
            FROM ingestion_runs
            WHERE raw_file_id = %s
            """,
            (raw_file_id,),
        )
        assert cur.fetchone() == (
            "failed",
            requested_date,
            requested_date,
            date(2026, 3, 17),
        )
        cur.execute("SELECT COUNT(*) FROM ingestion_locks WHERE fecha_datos = %s", (requested_date,))
        assert cur.fetchone()[0] == 0


def test_partial_fact_load_failure_remains_retryable_and_releases_the_lock(
    pg_conn, sample_zip_path, tmp_path, monkeypatch
):
    raw_file_id = _create_raw_file_for_date(
        pg_conn, _RUN_DATE, "partial-fact-load-failure"
    )
    pg_conn.commit()
    original_run_all = pipeline.quality_checks.run_all

    monkeypatch.setattr(pipeline, "_db_connect", lambda: _connect_to_fixture(pg_conn))
    monkeypatch.setattr(
        pipeline.downloader,
        "download",
        lambda conn, raw_dir, fecha: DownloadResult(
            raw_file_id=raw_file_id,
            filename="partial-fact-load-failure.zip",
            path=sample_zip_path,
            sha256="partial-fact-load-failure",
            size_bytes=sample_zip_path.stat().st_size,
            fecha_datos=_RUN_DATE,
            is_new=True,
        ),
    )
    monkeypatch.setattr(
        pipeline.quality_checks,
        "run_all",
        lambda conn, run_id: original_run_all(conn, run_id, min_productos=1),
    )
    monkeypatch.setattr(
        pipeline,
        "stream_pending_product_facts",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("fact load interrupted")),
    )
    monkeypatch.setenv("SEPA_DEV_CHAIN_KEYS", "1:1")
    monkeypatch.setenv("SEPA_DEV_MAX_PRODUCT_ROWS", "2")

    with pytest.raises(RuntimeError, match="fact load interrupted"):
        pipeline.run(fecha=_RUN_DATE, raw_dir=tmp_path)

    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT is_partial, status, finished_at IS NOT NULL, error_message
            FROM ingestion_runs
            WHERE raw_file_id = %s
            """,
            (raw_file_id,),
        )
        is_partial, status, finished, message = cur.fetchone()
        assert (is_partial, status, finished) == (True, "failed", True)
        assert "fact load interrupted" in message
        cur.execute("SELECT COUNT(*) FROM fact_precios WHERE fecha_id = %s", (20260317,))
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT COUNT(*) FROM ingestion_locks WHERE fecha_datos = %s", (_RUN_DATE,))
        assert cur.fetchone()[0] == 0

    assert should_process_raw_file(pg_conn, raw_file_id, partial=True) is True
    assert should_process_raw_file(pg_conn, raw_file_id) is True


def test_main_exits_nonzero_when_the_pipeline_fails(monkeypatch):
    def fail_pipeline(**kwargs):
        raise RuntimeError("pipeline failed")

    monkeypatch.setattr(pipeline, "configure_logging", lambda **_: None)
    monkeypatch.setattr(pipeline, "run", fail_pipeline)
    monkeypatch.setattr(sys, "argv", ["run_pipeline"])

    with pytest.raises(SystemExit) as exc_info:
        pipeline.main()

    assert exc_info.value.code == 1
