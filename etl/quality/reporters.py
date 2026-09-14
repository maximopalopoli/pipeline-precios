"""
reporters.py — Structured logging and persistence for quality check results.

Consumes the list[CheckResult] produced by checks.run_all() and:

  1. Logs each individual result at the appropriate structlog level
     (info → passed, warning → failed WARNING, error → failed CRITICAL).
  2. Emits a single summary event with aggregate counts.
  3. Optionally persists a compact text summary into
     ingestion_runs.error_message (non-empty only when there are failures).
  4. Optionally inserts individual bad rows into rejected_rows so the
     transform stage can skip them by joining on that table.

Usage::

    from etl.quality import checks, reporters

    try:
        results = checks.run_all(conn, run_id)
    except checks.QualityCheckError as exc:
        reporters.report(exc.failures or results, run_id, conn=conn)
        raise

    reporters.report(results, run_id, conn=conn)
"""

from __future__ import annotations

import json
from dataclasses import dataclass
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

    When *conn* is supplied:
      - Failed checks are written to ingestion_runs.error_message.
      - CRITICAL failures also set status='failed' and finished_at.
      - Individual bad rows from failed WARNING checks are inserted into
        rejected_rows and ingestion_runs.rows_rejected is updated.

    Parameters
    ----------
    results:
        The list returned by checks.run_all() (or a subset of checks).
    run_id:
        ingestion_runs.id — used for log context and DB persistence.
    conn:
        Optional open psycopg2 connection.

    Returns
    -------
    QualityReport with aggregate counts.
    """
    qr = _build(results, run_id)
    _log_results(results, run_id)
    _log_summary(qr)
    if conn is not None:
        _persist(conn, qr, results)
        register_rejected_rows(conn, run_id, results)
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


# ── Rejected rows ──────────────────────────────────────────────────────────────

# Required columns per staging table (empty string = missing value)
_REQUIRED_COMERCIOS = [
    "id_comercio", "id_bandera", "comercio_cuit", "comercio_razon_social",
]
_REQUIRED_SUCURSALES = [
    "id_comercio", "id_bandera", "id_sucursal", "sucursales_nombre",
    "sucursales_tipo", "sucursales_localidad", "sucursales_provincia",
]
_REQUIRED_PRODUCTOS = [
    "id_comercio", "id_bandera", "id_sucursal", "id_producto",
    "productos_descripcion", "productos_marca", "productos_precio_lista",
]

# Argentina bounding box (must stay in sync with checks.py)
_ARG_LAT_MIN, _ARG_LAT_MAX = -55.1, -21.8
_ARG_LON_MIN, _ARG_LON_MAX = -73.6, -53.6

_VALID_TIPOS = ("Hipermercado", "Supermercado", "Autoservicio", "Tradicional", "Web")

_VALID_PROVINCIAS = (
    "AR-B", "AR-C", "AR-K", "AR-H", "AR-U", "AR-X", "AR-W", "AR-E",
    "AR-P", "AR-Y", "AR-L", "AR-F", "AR-M", "AR-N", "AR-Q", "AR-R",
    "AR-A", "AR-J", "AR-D", "AR-Z", "AR-S", "AR-G", "AR-V", "AR-T",
)

# Row tuple type: (run_id, source_file, raw_data, rejection_reason)
_RejectedRow = tuple[int, str, str, str]


def register_rejected_rows(
    conn: "psycopg2.connection",
    run_id: int,
    results: list[CheckResult],
) -> int:
    """
    Insert individual bad staging rows into rejected_rows.

    Only processes failed WARNING checks — CRITICAL failures abort the
    pipeline before rows are worth persisting.  Updates
    ingestion_runs.rows_rejected with the total count.

    Returns the number of rows inserted.
    """
    handlers = {
        "positive_prices": _reject_positive_prices,
        "required_nulls":  _reject_required_nulls,
        "coordinates_bbox": _reject_coordinates_bbox,
        "tipo_sucursal":   _reject_tipo_sucursal,
        "provincia":       _reject_provincia,
    }

    rows: list[_RejectedRow] = []
    for result in results:
        if result.passed or result.severity != Severity.WARNING:
            continue
        handler = handlers.get(result.name)
        if handler is None:
            continue
        rows.extend(handler(conn, run_id))

    if not rows:
        return 0

    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO rejected_rows (run_id, source_file, raw_data, rejection_reason)
            VALUES (%s, %s, %s, %s)
            """,
            rows,
        )
        cur.execute(
            "UPDATE ingestion_runs SET rows_rejected = %s WHERE id = %s",
            (len(rows), run_id),
        )
    conn.commit()
    log.info("quality.rejected_rows.registered", run_id=run_id, count=len(rows))
    return len(rows)


def _reject_positive_prices(
    conn: "psycopg2.connection",
    run_id: int,
) -> list[_RejectedRow]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id_comercio, id_bandera, id_sucursal, id_producto,
                   productos_precio_lista
              FROM stg_productos
             WHERE run_id = %s
               AND (
                   productos_precio_lista = ''
                   OR productos_precio_lista !~ '^[0-9]+(\\.[0-9]+)?$'
                   OR productos_precio_lista::numeric <= 0
               )
            """,
            (run_id,),
        )
        return [
            (
                run_id,
                "stg_productos",
                "|".join(str(v) for v in row),
                f"precio_lista inválido: {row[4]!r}",
            )
            for row in cur.fetchall()
        ]


def _reject_required_nulls(
    conn: "psycopg2.connection",
    run_id: int,
) -> list[_RejectedRow]:
    rows: list[_RejectedRow] = []
    table_specs = [
        ("stg_comercios",  _REQUIRED_COMERCIOS,  ["id_comercio", "id_bandera"]),
        ("stg_sucursales", _REQUIRED_SUCURSALES, ["id_comercio", "id_bandera", "id_sucursal"]),
        ("stg_productos",  _REQUIRED_PRODUCTOS,  ["id_comercio", "id_bandera", "id_sucursal", "id_producto"]),
    ]
    with conn.cursor() as cur:
        for table, required_cols, id_cols in table_specs:
            for col in required_cols:
                cur.execute(
                    f"""
                    SELECT {", ".join(id_cols)}, %s AS bad_col
                      FROM {table}
                     WHERE run_id = %s
                       AND (TRIM({col}) = '' OR {col} IS NULL)
                    """,
                    (col, run_id),
                )
                for row in cur.fetchall():
                    id_part = "|".join(str(v) for v in row[:-1])
                    bad_col = row[-1]
                    rows.append((
                        run_id,
                        table,
                        id_part,
                        f"campo requerido vacío: {bad_col}",
                    ))
    return rows


def _reject_coordinates_bbox(
    conn: "psycopg2.connection",
    run_id: int,
) -> list[_RejectedRow]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id_comercio, id_bandera, id_sucursal,
                   sucursales_latitud, sucursales_longitud
              FROM stg_sucursales
             WHERE run_id = %s
               AND sucursales_latitud  <> ''
               AND sucursales_longitud <> ''
               AND sucursales_latitud  ~ '^-?[0-9]+(\\.[0-9]+)?$'
               AND sucursales_longitud ~ '^-?[0-9]+(\\.[0-9]+)?$'
               AND NOT (
                   sucursales_latitud::numeric  BETWEEN %s AND %s
                   AND sucursales_longitud::numeric BETWEEN %s AND %s
               )
            """,
            (run_id, _ARG_LAT_MIN, _ARG_LAT_MAX, _ARG_LON_MIN, _ARG_LON_MAX),
        )
        return [
            (
                run_id,
                "stg_sucursales",
                "|".join(str(v) for v in row),
                f"coordenadas fuera del bounding box de Argentina: lat={row[3]} lon={row[4]}",
            )
            for row in cur.fetchall()
        ]


def _reject_tipo_sucursal(
    conn: "psycopg2.connection",
    run_id: int,
) -> list[_RejectedRow]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id_comercio, id_bandera, id_sucursal, sucursales_tipo
              FROM stg_sucursales
             WHERE run_id = %s
               AND sucursales_tipo <> ''
               AND sucursales_tipo != ALL(%s)
            """,
            (run_id, list(_VALID_TIPOS)),
        )
        return [
            (
                run_id,
                "stg_sucursales",
                "|".join(str(v) for v in row),
                f"tipo de sucursal inválido: {row[3]!r}",
            )
            for row in cur.fetchall()
        ]


def _reject_provincia(
    conn: "psycopg2.connection",
    run_id: int,
) -> list[_RejectedRow]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id_comercio, id_bandera, id_sucursal, sucursales_provincia
              FROM stg_sucursales
             WHERE run_id = %s
               AND sucursales_provincia <> ''
               AND sucursales_provincia != ALL(%s)
            """,
            (run_id, list(_VALID_PROVINCIAS)),
        )
        return [
            (
                run_id,
                "stg_sucursales",
                "|".join(str(v) for v in row),
                f"provincia inválida: {row[3]!r}",
            )
            for row in cur.fetchall()
        ]
