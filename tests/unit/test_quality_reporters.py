"""
Unit tests for etl/quality/reporters.py.

The reporter has no external dependencies beyond the CheckResult dataclass,
so most tests are pure unit tests (no DB required).  A small integration
section verifies _persist() using the pg_conn fixture.
"""

import json

import pytest

from etl.quality.checks import CheckResult, Severity
from etl.quality.reporters import QualityReport, build, report


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


def _insert_raw_file(conn) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO raw_files (filename, url, sha256, size_bytes, fecha_datos)
            VALUES ('r.zip', 'http://test', 'abc123', 0, '2026-03-19')
            RETURNING id
            """,
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
