"""
reporters.py — Structured logging and persistence for quality check results.

Consumes the list[CheckResult] produced by checks.run_all() and:

  1. Logs each individual result at the appropriate structlog level
     (info → passed, warning → failed WARNING, error → failed CRITICAL).
  2. Emits a single summary event with aggregate counts.
  3. Optionally persists a compact text summary into
     ingestion_runs.error_message so failures are visible without reading
     logs.

Usage::

    from etl.quality import checks, reporters

    try:
        results = checks.run_all(conn, run_id)
    except checks.QualityCheckError:
        pass  # individual check logs already emitted; re-raise after report

    report = reporters.report(results, run_id, conn=conn)
    if not report.ok:
        raise SystemExit(1)
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

import structlog

from etl.quality.checks import CheckResult, Severity

if TYPE_CHECKING:
    import psycopg2

log = structlog.get_logger(__name__)


# ── Report dataclass ───────────────────────────────────────────────────────────


@dataclass
class QualityReport:
    run_id: int
    total: int
    passed: int
    warnings_failed: int   # failed checks with severity WARNING
    criticals_failed: int  # failed checks with severity CRITICAL

    @property
    def ok(self) -> bool:
        """True when no CRITICAL check failed (warnings are tolerated)."""
        return self.criticals_failed == 0

    @property
    def clean(self) -> bool:
        """True when every single check passed."""
        return self.passed == self.total


# ── Public API ─────────────────────────────────────────────────────────────────


def report(
    results: list[CheckResult],
    run_id: int,
    *,
    conn: "psycopg2.connection | None" = None,
) -> QualityReport:
    """
    Log, summarise, and optionally persist quality check results.

    Parameters
    ----------
    results:
        The list returned by checks.run_all() (or a subset of checks).
    run_id:
        ingestion_runs.id — used for log context and DB persistence.
    conn:
        Optional open psycopg2 connection.  When supplied, the report
        summary is written to ingestion_runs.error_message (non-empty
        only when there are failures).

    Returns
    -------
    QualityReport with aggregate counts.
    """
    qr = _build(results, run_id)
    _log_results(results, run_id)
    _log_summary(qr)
    if conn is not None:
        _persist(conn, qr, results)
    return qr


def build(results: list[CheckResult], run_id: int) -> QualityReport:
    """Return a QualityReport without logging or persisting."""
    return _build(results, run_id)


# ── Private helpers ────────────────────────────────────────────────────────────


def _build(results: list[CheckResult], run_id: int) -> QualityReport:
    passed = sum(1 for r in results if r.passed)
    warnings_failed = sum(
        1 for r in results if not r.passed and r.severity == Severity.WARNING
    )
    criticals_failed = sum(
        1 for r in results if not r.passed and r.severity == Severity.CRITICAL
    )
    return QualityReport(
        run_id=run_id,
        total=len(results),
        passed=passed,
        warnings_failed=warnings_failed,
        criticals_failed=criticals_failed,
    )


def _log_results(results: list[CheckResult], run_id: int) -> None:
    """Emit one structured log event per check at the appropriate level."""
    logger = log.bind(run_id=run_id)
    for r in results:
        bound = logger.bind(
            check=r.name,
            severity=r.severity,
            passed=r.passed,
            **r.details,
        )
        if r.passed:
            bound.info("quality.check.result")
        elif r.severity == Severity.WARNING:
            bound.warning("quality.check.result")
        else:
            bound.error("quality.check.result")


def _log_summary(qr: QualityReport) -> None:
    """Emit a single summary event after all individual events."""
    level_fn = log.info if qr.ok else log.error
    level_fn(
        "quality.report.summary",
        run_id=qr.run_id,
        total=qr.total,
        passed=qr.passed,
        warnings_failed=qr.warnings_failed,
        criticals_failed=qr.criticals_failed,
        ok=qr.ok,
    )


def mark_run_failed(
    conn: "psycopg2.connection",
    run_id: int,
    message: str,
) -> None:
    """
    Set ingestion_runs.status = 'failed' and record *message* as the error.

    Also stamps finished_at so duration_seconds is computed correctly.
    Safe to call from any pipeline stage (not only quality checks).
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE ingestion_runs
               SET status        = 'failed',
                   finished_at   = NOW(),
                   error_message = %s
             WHERE id = %s
            """,
            (message, run_id),
        )
    conn.commit()
    log.error("pipeline.run.failed", run_id=run_id)


def _persist(
    conn: "psycopg2.connection",
    qr: QualityReport,
    results: list[CheckResult],
) -> None:
    """
    Persist failure details and, for CRITICAL failures, mark the run failed.

    - Any failure → write compact JSON to ingestion_runs.error_message.
    - CRITICAL failure → also set status = 'failed' and finished_at = NOW().
    - All passed → no-op.
    """
    failed = [r for r in results if not r.passed]
    if not failed:
        return

    payload = {
        "quality_failures": [
            {
                "check": r.name,
                "severity": r.severity,
                "details": r.details,
            }
            for r in failed
        ]
    }
    summary_text = json.dumps(payload, ensure_ascii=False)

    if qr.criticals_failed > 0:
        # mark_run_failed writes error_message + status + finished_at atomically
        mark_run_failed(conn, qr.run_id, summary_text)
    else:
        # Warnings only — record the message but keep status as-is
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE ingestion_runs SET error_message = %s WHERE id = %s",
                (summary_text, qr.run_id),
            )
        conn.commit()

    log.info(
        "quality.report.persisted",
        run_id=qr.run_id,
        failed_checks=len(failed),
        criticals_failed=qr.criticals_failed,
    )
