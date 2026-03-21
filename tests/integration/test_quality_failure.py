"""
Integration test: critical quality check failure → ingestion_run marked 'failed'.

Scenario
--------
After loading sample.zip into staging, stg_productos is wiped to trigger
the CRITICAL min_row_count check failure.  The test verifies that:

1. quality_checks.run_all() raises QualityCheckError.
2. reporters.report() persists the failure and sets status='failed'.
3. ingestion_runs.error_message contains the failing check name(s).
4. A duplicate-PK injection also triggers a CRITICAL failure.
"""

from pathlib import Path

import pytest

from etl.quality import checks as quality_checks
from etl.quality import reporters
from etl.staging.loader import load

# ── Helpers ────────────────────────────────────────────────────────────────────

_raw_file_counter = 0


def _insert_raw_file(conn) -> int:
    global _raw_file_counter
    _raw_file_counter += 1
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO raw_files (filename, url, sha256, size_bytes, fecha_datos)"
            " VALUES ('fail.zip', 'http://test', %s, 1, '2026-03-20') RETURNING id",
            (f"sha_qfail_{_raw_file_counter}",),
        )
        raw_id = cur.fetchone()[0]
    conn.commit()
    return raw_id


def _insert_run(conn, raw_id: int) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ingestion_runs (raw_file_id, status)"
            " VALUES (%s, 'running') RETURNING id",
            (raw_id,),
        )
        run_id = cur.fetchone()[0]
    conn.commit()
    return run_id


def _setup_run(conn, sample_zip_path) -> int:
    """Insert raw_file + ingestion_run, load sample.zip, return run_id."""
    raw_id = _insert_raw_file(conn)
    run_id = _insert_run(conn, raw_id)
    load(conn, sample_zip_path, run_id)
    return run_id


def _fetch_run(conn, run_id: int) -> tuple:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT status, error_message FROM ingestion_runs WHERE id = %s",
            (run_id,),
        )
        return cur.fetchone()


# ── Tests ──────────────────────────────────────────────────────────────────────


class TestCriticalQualityFailureMarksFailed:

    def test_empty_staging_marks_run_failed(self, pg_conn, sample_zip_path):
        """
        Wipe stg_productos after loading → min_row_count CRITICAL check fails.
        reporters.report() must set ingestion_runs.status = 'failed'.
        """
        run_id = _setup_run(pg_conn, sample_zip_path)

        with pg_conn.cursor() as cur:
            cur.execute("DELETE FROM stg_productos WHERE run_id = %s", (run_id,))
        pg_conn.commit()

        with pytest.raises(quality_checks.QualityCheckError) as exc_info:
            quality_checks.run_all(pg_conn, run_id, min_productos=1)

        reporters.report(results=exc_info.value.failures, run_id=run_id, conn=pg_conn)

        status, error_message = _fetch_run(pg_conn, run_id)
        assert status == "failed"
        assert error_message is not None and len(error_message) > 0

    def test_error_message_contains_failing_check(self, pg_conn, sample_zip_path):
        """error_message JSON must mention at least one check name."""
        run_id = _setup_run(pg_conn, sample_zip_path)

        with pg_conn.cursor() as cur:
            cur.execute("DELETE FROM stg_productos WHERE run_id = %s", (run_id,))
        pg_conn.commit()

        with pytest.raises(quality_checks.QualityCheckError) as exc_info:
            quality_checks.run_all(pg_conn, run_id, min_productos=1)

        reporters.report(results=exc_info.value.failures, run_id=run_id, conn=pg_conn)

        _, error_message = _fetch_run(pg_conn, run_id)
        # The JSON payload must include at least one of these check names
        critical_check_names = {"min_row_count", "fk_integrity", "pk_uniqueness", "schema"}
        assert any(name in error_message for name in critical_check_names), (
            f"error_message {error_message!r} does not mention any critical check"
        )

    def test_all_failures_have_critical_severity(self, pg_conn, sample_zip_path):
        """QualityCheckError.failures must contain only CRITICAL results."""
        run_id = _setup_run(pg_conn, sample_zip_path)

        with pg_conn.cursor() as cur:
            cur.execute("DELETE FROM stg_productos WHERE run_id = %s", (run_id,))
        pg_conn.commit()

        with pytest.raises(quality_checks.QualityCheckError) as exc_info:
            quality_checks.run_all(pg_conn, run_id, min_productos=1)

        assert len(exc_info.value.failures) > 0
        assert all(
            f.severity == quality_checks.Severity.CRITICAL
            for f in exc_info.value.failures
        )

    def test_duplicate_pk_is_warning_not_critical(self, pg_conn, sample_zip_path):
        """
        Injecting a duplicate comercio row triggers pk_uniqueness WARNING.
        The pipeline must NOT abort — duplicate PKs in SEPA source data are
        common and handled by the transform stage via rejected_rows.
        """
        run_id = _setup_run(pg_conn, sample_zip_path)

        # Duplicate the first comercio row to create a PK collision
        with pg_conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO stg_comercios (
                    run_id, id_comercio, id_bandera, comercio_cuit,
                    comercio_razon_social, comercio_bandera_nombre,
                    comercio_bandera_url, comercio_ultima_actualizacion,
                    comercio_version_sepa
                )
                SELECT
                    run_id, id_comercio, id_bandera, comercio_cuit,
                    comercio_razon_social, comercio_bandera_nombre,
                    comercio_bandera_url, comercio_ultima_actualizacion,
                    comercio_version_sepa
                  FROM stg_comercios
                 WHERE run_id = %s
                 LIMIT 1
                """,
                (run_id,),
            )
        pg_conn.commit()

        # Should NOT raise — pk_uniqueness is WARNING, not CRITICAL
        results = quality_checks.run_all(pg_conn, run_id, min_productos=5)

        failing = [r for r in results if not r.passed]
        assert any(r.name == "pk_uniqueness" for r in failing)
        pk_result = next(r for r in failing if r.name == "pk_uniqueness")
        assert pk_result.severity == quality_checks.Severity.WARNING

    def test_finished_at_is_set_on_failure(self, pg_conn, sample_zip_path):
        """finished_at must be stamped so duration_seconds is computable."""
        run_id = _setup_run(pg_conn, sample_zip_path)

        with pg_conn.cursor() as cur:
            cur.execute("DELETE FROM stg_productos WHERE run_id = %s", (run_id,))
        pg_conn.commit()

        with pytest.raises(quality_checks.QualityCheckError) as exc_info:
            quality_checks.run_all(pg_conn, run_id, min_productos=1)

        reporters.report(results=exc_info.value.failures, run_id=run_id, conn=pg_conn)

        with pg_conn.cursor() as cur:
            cur.execute(
                "SELECT finished_at FROM ingestion_runs WHERE id = %s", (run_id,)
            )
            finished_at = cur.fetchone()[0]

        assert finished_at is not None, "finished_at must be set when run fails"
