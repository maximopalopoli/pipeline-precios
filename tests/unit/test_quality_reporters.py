"""
Unit tests for etl/quality/reporters.py.

The reporter has no external dependencies beyond the CheckResult dataclass,
so most tests are pure unit tests (no DB required).  A small integration
section verifies _persist() using the pg_conn fixture.
"""

import json


from etl.quality.checks import CheckResult, Severity
from etl.quality.reporters import (
    QualityReport,
    build,
    mark_run_failed,
    register_rejected_rows,
    report,
)


# ── Fixtures ───────────────────────────────────────────────────────────────────

def _make(name: str, passed: bool, severity: Severity, **details) -> CheckResult:
    return CheckResult(name=name, passed=passed, severity=severity, details=details)


ALL_PASS = [
    _make("schema_match",    passed=True,  severity=Severity.CRITICAL),
    _make("pk_uniqueness",   passed=True,  severity=Severity.CRITICAL),
    _make("fk_integrity",    passed=True,  severity=Severity.CRITICAL),
    _make("positive_prices", passed=True,  severity=Severity.WARNING),
    _make("min_row_count",   passed=True,  severity=Severity.CRITICAL),
]

WARN_ONLY = [
    _make("schema_match",    passed=True,  severity=Severity.CRITICAL),
    _make("positive_prices", passed=False, severity=Severity.WARNING,
          bad_precio_lista_rows=3, total_rows=100),
]

CRITICAL_AND_WARN = [
    _make("schema_match",    passed=False, severity=Severity.CRITICAL,
          missing_cols={"stg_comercios": ["comercio_cuit"]}, extra_cols={}),
    _make("positive_prices", passed=False, severity=Severity.WARNING,
          bad_precio_lista_rows=5, total_rows=200),
    _make("pk_uniqueness",   passed=True,  severity=Severity.CRITICAL),
]


# ── build() ────────────────────────────────────────────────────────────────────


def test_build_all_pass():
    qr = build(ALL_PASS, run_id=1)
    assert qr.total == 5
    assert qr.passed == 5
    assert qr.warnings_failed == 0
    assert qr.criticals_failed == 0


def test_build_warning_only():
    qr = build(WARN_ONLY, run_id=2)
    assert qr.total == 2
    assert qr.passed == 1
    assert qr.warnings_failed == 1
    assert qr.criticals_failed == 0


def test_build_mixed_failures():
    qr = build(CRITICAL_AND_WARN, run_id=3)
    assert qr.total == 3
    assert qr.passed == 1
    assert qr.warnings_failed == 1
    assert qr.criticals_failed == 1


# ── QualityReport.ok / .clean ──────────────────────────────────────────────────


def test_ok_true_when_no_critical_failures():
    qr = build(WARN_ONLY, run_id=1)
    assert qr.ok is True


def test_ok_false_when_critical_failed():
    qr = build(CRITICAL_AND_WARN, run_id=1)
    assert qr.ok is False


def test_clean_true_when_everything_passes():
    qr = build(ALL_PASS, run_id=1)
    assert qr.clean is True


def test_clean_false_when_warning_fails():
    qr = build(WARN_ONLY, run_id=1)
    assert qr.clean is False


# ── report() without DB — smoke test (logging only) ───────────────────────────


def test_report_returns_quality_report():
    qr = report(ALL_PASS, run_id=42)
    assert isinstance(qr, QualityReport)
    assert qr.run_id == 42


def test_report_no_conn_does_not_raise():
    # Should not raise even when conn=None and there are failures
    qr = report(CRITICAL_AND_WARN, run_id=1, conn=None)
    assert qr.criticals_failed == 1


# ── Integration: _persist via report(conn=...) ─────────────────────────────────


_raw_file_counter = 0


def _insert_raw_file(conn) -> int:
    global _raw_file_counter
    _raw_file_counter += 1
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO raw_files (filename, url, sha256, size_bytes, fecha_datos)
            VALUES ('r.zip', 'http://test', %s, 0, '2026-03-19')
            RETURNING id
            """,
            (f"abc123-{_raw_file_counter}",),
        )
        fid = cur.fetchone()[0]
    conn.commit()
    return fid


def _insert_run(conn, raw_file_id: int) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ingestion_runs (raw_file_id) VALUES (%s) RETURNING id",
            (raw_file_id,),
        )
        run_id = cur.fetchone()[0]
    conn.commit()
    return run_id


def test_report_does_not_write_error_message_when_all_pass(pg_conn):
    raw_file_id = _insert_raw_file(pg_conn)
    run_id = _insert_run(pg_conn, raw_file_id)

    report(ALL_PASS, run_id, conn=pg_conn)

    with pg_conn.cursor() as cur:
        cur.execute("SELECT error_message FROM ingestion_runs WHERE id = %s", (run_id,))
        error_message = cur.fetchone()[0]

    assert error_message is None


def test_report_writes_error_message_on_failure(pg_conn):
    raw_file_id = _insert_raw_file(pg_conn)
    run_id = _insert_run(pg_conn, raw_file_id)

    report(CRITICAL_AND_WARN, run_id, conn=pg_conn)

    with pg_conn.cursor() as cur:
        cur.execute("SELECT error_message FROM ingestion_runs WHERE id = %s", (run_id,))
        error_message = cur.fetchone()[0]

    assert error_message is not None
    payload = json.loads(error_message)
    assert "quality_failures" in payload
    failed_names = {f["check"] for f in payload["quality_failures"]}
    assert failed_names == {"schema_match", "positive_prices"}


def test_report_persisted_json_includes_severity_and_details(pg_conn):
    raw_file_id = _insert_raw_file(pg_conn)
    run_id = _insert_run(pg_conn, raw_file_id)

    report(WARN_ONLY, run_id, conn=pg_conn)

    with pg_conn.cursor() as cur:
        cur.execute("SELECT error_message FROM ingestion_runs WHERE id = %s", (run_id,))
        error_message = cur.fetchone()[0]

    payload = json.loads(error_message)
    failure = payload["quality_failures"][0]
    assert failure["check"] == "positive_prices"
    assert failure["severity"] == Severity.WARNING
    assert failure["details"]["bad_precio_lista_rows"] == 3


def test_report_sets_status_failed_on_critical(pg_conn):
    raw_file_id = _insert_raw_file(pg_conn)
    run_id = _insert_run(pg_conn, raw_file_id)

    report(CRITICAL_AND_WARN, run_id, conn=pg_conn)

    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT status, finished_at FROM ingestion_runs WHERE id = %s",
            (run_id,),
        )
        status, finished_at = cur.fetchone()

    assert status == "failed"
    assert finished_at is not None


def test_report_does_not_set_status_failed_on_warning_only(pg_conn):
    raw_file_id = _insert_raw_file(pg_conn)
    run_id = _insert_run(pg_conn, raw_file_id)

    report(WARN_ONLY, run_id, conn=pg_conn)

    with pg_conn.cursor() as cur:
        cur.execute("SELECT status FROM ingestion_runs WHERE id = %s", (run_id,))
        status = cur.fetchone()[0]

    # Warnings should not change the run status ('running' is the default)
    assert status == "running"


# ── mark_run_failed() ──────────────────────────────────────────────────────────


def test_mark_run_failed_sets_status_and_message(pg_conn):
    raw_file_id = _insert_raw_file(pg_conn)
    run_id = _insert_run(pg_conn, raw_file_id)

    mark_run_failed(pg_conn, run_id, "something went wrong")

    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT status, error_message, finished_at FROM ingestion_runs WHERE id = %s",
            (run_id,),
        )
        status, error_message, finished_at = cur.fetchone()

    assert status == "failed"
    assert error_message == "something went wrong"
    assert finished_at is not None


def test_mark_run_failed_stamps_finished_at(pg_conn):
    raw_file_id = _insert_raw_file(pg_conn)
    run_id = _insert_run(pg_conn, raw_file_id)

    with pg_conn.cursor() as cur:
        cur.execute("SELECT finished_at FROM ingestion_runs WHERE id = %s", (run_id,))
        assert cur.fetchone()[0] is None  # not yet finished

    mark_run_failed(pg_conn, run_id, "abort")

    with pg_conn.cursor() as cur:
        cur.execute("SELECT finished_at FROM ingestion_runs WHERE id = %s", (run_id,))
        assert cur.fetchone()[0] is not None


# ── register_rejected_rows() ───────────────────────────────────────────────────


def _load_sample_staging(conn, sample_zip_path) -> int:
    """Load sample.zip into staging and return run_id."""
    from etl.staging.loader import load
    raw_file_id = _insert_raw_file(conn)
    run_id = _insert_run(conn, raw_file_id)
    load(conn, sample_zip_path, run_id)
    return run_id


def test_register_rejected_rows_returns_zero_when_all_pass(pg_conn, sample_zip_path):
    run_id = _load_sample_staging(pg_conn, sample_zip_path)
    count = register_rejected_rows(pg_conn, run_id, ALL_PASS)
    assert count == 0

    with pg_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM rejected_rows WHERE run_id = %s", (run_id,))
        assert cur.fetchone()[0] == 0


def test_register_rejected_rows_skips_critical_failures(pg_conn, sample_zip_path):
    """CRITICAL failures must not produce rejected_rows entries."""
    run_id = _load_sample_staging(pg_conn, sample_zip_path)
    critical_only = [_make("schema_match", passed=False, severity=Severity.CRITICAL)]
    count = register_rejected_rows(pg_conn, run_id, critical_only)
    assert count == 0


def test_register_rejected_rows_positive_prices(pg_conn, sample_zip_path):
    run_id = _load_sample_staging(pg_conn, sample_zip_path)

    # Corrupt one precio_lista
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            UPDATE stg_productos SET productos_precio_lista = '0'
             WHERE ctid = (SELECT ctid FROM stg_productos WHERE run_id = %s LIMIT 1)
            """,
            (run_id,),
        )
    pg_conn.commit()

    results = [_make("positive_prices", passed=False, severity=Severity.WARNING,
                     bad_precio_lista_rows=1, total_rows=7)]
    count = register_rejected_rows(pg_conn, run_id, results)
    assert count == 1

    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT source_file, rejection_reason FROM rejected_rows WHERE run_id = %s",
            (run_id,),
        )
        row = cur.fetchone()
    assert row[0] == "stg_productos"
    assert "precio_lista" in row[1]


def test_register_rejected_rows_required_nulls(pg_conn, sample_zip_path):
    run_id = _load_sample_staging(pg_conn, sample_zip_path)

    with pg_conn.cursor() as cur:
        cur.execute(
            "UPDATE stg_comercios SET comercio_cuit = '' WHERE run_id = %s",
            (run_id,),
        )
    pg_conn.commit()

    results = [_make("required_nulls", passed=False, severity=Severity.WARNING,
                     nulls_by_table={"stg_comercios": {"comercio_cuit": 2}})]
    count = register_rejected_rows(pg_conn, run_id, results)
    assert count == 2  # sample has 2 comercios

    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT source_file, rejection_reason FROM rejected_rows WHERE run_id = %s",
            (run_id,),
        )
        row = cur.fetchone()
    assert row[0] == "stg_comercios"
    assert "comercio_cuit" in row[1]


def test_register_rejected_rows_counts_all_but_samples_a_bounded_set_once(
    pg_conn, sample_zip_path
):
    run_id = _load_sample_staging(pg_conn, sample_zip_path)

    with pg_conn.cursor() as cur:
        cur.execute(
            "UPDATE stg_comercios SET comercio_cuit = '' WHERE run_id = %s",
            (run_id,),
        )
    pg_conn.commit()

    results = [_make("required_nulls", passed=False, severity=Severity.WARNING,
                     nulls_by_table={"stg_comercios": {"comercio_cuit": 2}})]
    register_rejected_rows(pg_conn, run_id, results, sample_rows=1)
    count = register_rejected_rows(pg_conn, run_id, results, sample_rows=1)

    assert count == 2
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT source_file, rejection_reason, row_count
            FROM rejection_counts WHERE run_id = %s
            """,
            (run_id,),
        )
        assert cur.fetchall() == [("stg_comercios", "comercio_cuit_empty", 2)]
        cur.execute("SELECT COUNT(*) FROM rejected_rows WHERE run_id = %s", (run_id,))
        assert cur.fetchone()[0] == 1
        cur.execute("SELECT rows_rejected FROM ingestion_runs WHERE id = %s", (run_id,))
        assert cur.fetchone()[0] == 2


def test_register_rejected_rows_tipo_sucursal(pg_conn, sample_zip_path):
    run_id = _load_sample_staging(pg_conn, sample_zip_path)

    with pg_conn.cursor() as cur:
        cur.execute(
            """
            UPDATE stg_sucursales SET sucursales_tipo = 'Megamercado'
             WHERE ctid = (SELECT ctid FROM stg_sucursales WHERE run_id = %s LIMIT 1)
            """,
            (run_id,),
        )
    pg_conn.commit()

    results = [_make("tipo_sucursal", passed=False, severity=Severity.WARNING,
                     invalid_tipos={"Megamercado": 1})]
    count = register_rejected_rows(pg_conn, run_id, results)
    assert count == 1

    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT rejection_reason FROM rejected_rows WHERE run_id = %s",
            (run_id,),
        )
        reason = cur.fetchone()[0]
    assert "Megamercado" in reason


def test_register_rejected_rows_updates_rows_rejected(pg_conn, sample_zip_path):
    run_id = _load_sample_staging(pg_conn, sample_zip_path)

    with pg_conn.cursor() as cur:
        cur.execute(
            """
            UPDATE stg_productos SET productos_precio_lista = '-1'
             WHERE ctid = (SELECT ctid FROM stg_productos WHERE run_id = %s LIMIT 1)
            """,
            (run_id,),
        )
    pg_conn.commit()

    results = [_make("positive_prices", passed=False, severity=Severity.WARNING,
                     bad_precio_lista_rows=1, total_rows=7)]
    register_rejected_rows(pg_conn, run_id, results)

    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT rows_rejected FROM ingestion_runs WHERE id = %s",
            (run_id,),
        )
        assert cur.fetchone()[0] == 1


def test_report_with_conn_registers_rejected_rows(pg_conn, sample_zip_path):
    """report() end-to-end: bad price → rejected_rows populated."""
    run_id = _load_sample_staging(pg_conn, sample_zip_path)

    with pg_conn.cursor() as cur:
        cur.execute(
            """
            UPDATE stg_productos SET productos_precio_lista = '0'
             WHERE ctid = (SELECT ctid FROM stg_productos WHERE run_id = %s LIMIT 1)
            """,
            (run_id,),
        )
    pg_conn.commit()

    results = [_make("positive_prices", passed=False, severity=Severity.WARNING,
                     bad_precio_lista_rows=1, total_rows=7)]
    qr = report(results, run_id, conn=pg_conn)

    assert qr.warnings_failed == 1
    with pg_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM rejected_rows WHERE run_id = %s", (run_id,))
        assert cur.fetchone()[0] == 1
