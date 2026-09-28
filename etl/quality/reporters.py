"""
reporters.py — Structured logging and persistence for quality check results.

Consumes the list[CheckResult] produced by checks.run_all() and:

  1. Logs each individual result at the appropriate structlog level
     (info → passed, warning → failed WARNING, error → failed CRITICAL).
  2. Emits a single summary event with aggregate counts.
  3. Optionally persists a compact text summary into
     ingestion_runs.error_message (non-empty only when there are failures).
  4. Optionally counts bad rows per reason in rejection_counts and keeps a
     bounded sample of them in rejected_rows.

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
from typing import TYPE_CHECKING, Callable

import structlog

from etl.ingestion_run import mark_run_failed
from etl.quality.checks import CheckResult, Severity
from etl.rejection_counts import (
    clear_rejections,
    record_rejections,
    sample_rows_from_environment,
)

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
      - Bad rows from failed WARNING checks are counted per reason, a bounded
        sample is kept in rejected_rows, and ingestion_runs.rows_rejected is
        updated.

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


_STAGING_TABLES = ("stg_comercios", "stg_sucursales", "stg_productos")


@dataclass(frozen=True)
class _RejectionQuery:
    """Staging rows rejected for one stable reason code.

    ``columns`` are joined with ``|`` as the sampled ``raw_data``; ``detail``
    builds the detailed reason of one sampled row from them.
    """

    table: str
    code: str
    columns: tuple[str, ...]
    condition: str
    detail: Callable[[tuple], str]
    params: tuple = ()


def register_rejected_rows(
    conn: "psycopg2.connection",
    run_id: int,
    results: list[CheckResult],
    *,
    sample_rows: int | None = None,
) -> int:
    """
    Count bad staging rows per reason and keep a bounded sample of them.

    Only processes failed WARNING checks — CRITICAL failures abort the
    pipeline before rows are worth recording.  Every bad row is counted in
    rejection_counts, at most ``sample_rows`` per table and reason are stored
    in rejected_rows, and ingestion_runs.rows_rejected gets the full total.

    Returns the number of rows counted.
    """
    if sample_rows is None:
        sample_rows = sample_rows_from_environment()
    handlers = {
        "positive_prices": _reject_positive_prices,
        "required_nulls":  _reject_required_nulls,
        "coordinates_bbox": _reject_coordinates_bbox,
        "tipo_sucursal":   _reject_tipo_sucursal,
        "provincia":       _reject_provincia,
    }

    clear_rejections(conn, run_id=run_id, source_files=_STAGING_TABLES)
    total = 0
    for result in results:
        if result.passed or result.severity != Severity.WARNING:
            continue
        handler = handlers.get(result.name)
        if handler is None:
            continue
        for query in handler():
            total += _record_rejection_query(conn, run_id, query, sample_rows)

    if total == 0:
        conn.commit()
        return 0

    with conn.cursor() as cur:
        cur.execute(
            "UPDATE ingestion_runs SET rows_rejected = %s WHERE id = %s",
            (total, run_id),
        )
    conn.commit()
    log.info("quality.rejected_rows.registered", run_id=run_id, count=total)
    return total


def _record_rejection_query(
    conn: "psycopg2.connection",
    run_id: int,
    query: _RejectionQuery,
    sample_rows: int,
) -> int:
    where = f"FROM {query.table} WHERE run_id = %s AND ({query.condition})"
    params = (run_id, *query.params)
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) {where}", params)
        count = cur.fetchone()[0]
        if count == 0:
            return 0
        rows: list[tuple] = []
        if sample_rows > 0:
            cur.execute(
                f"SELECT {', '.join(query.columns)} {where} LIMIT %s",
                (*params, sample_rows),
            )
            rows = cur.fetchall()
    return record_rejections(
        conn,
        run_id=run_id,
        source_file=query.table,
        counts={query.code: count},
        samples=(
            (query.code, "|".join(str(v) for v in row), query.detail(row))
            for row in rows
        ),
        sample_rows=sample_rows,
    )


def _reject_positive_prices() -> list[_RejectionQuery]:
    return [
        _RejectionQuery(
            table="stg_productos",
            code="precio_lista_invalid",
            columns=(
                "id_comercio", "id_bandera", "id_sucursal", "id_producto",
                "productos_precio_lista",
            ),
            condition="""
                productos_precio_lista = ''
                OR productos_precio_lista !~ '^[0-9]+(\\.[0-9]+)?$'
                OR productos_precio_lista::numeric <= 0
            """,
            detail=lambda row: f"precio_lista inválido: {row[4]!r}",
        )
    ]


def _reject_required_nulls() -> list[_RejectionQuery]:
    table_specs = [
        ("stg_comercios",  _REQUIRED_COMERCIOS,  ("id_comercio", "id_bandera")),
        ("stg_sucursales", _REQUIRED_SUCURSALES, ("id_comercio", "id_bandera", "id_sucursal")),
        ("stg_productos",  _REQUIRED_PRODUCTOS,  ("id_comercio", "id_bandera", "id_sucursal", "id_producto")),
    ]
    return [
        _RejectionQuery(
            table=table,
            code=f"{col}_empty",
            columns=id_cols,
            condition=f"TRIM({col}) = '' OR {col} IS NULL",
            detail=lambda row, col=col: f"campo requerido vacío: {col}",
        )
        for table, required_cols, id_cols in table_specs
        for col in required_cols
    ]


def _reject_coordinates_bbox() -> list[_RejectionQuery]:
    return [
        _RejectionQuery(
            table="stg_sucursales",
            code="coordinates_outside_argentina",
            columns=(
                "id_comercio", "id_bandera", "id_sucursal",
                "sucursales_latitud", "sucursales_longitud",
            ),
            condition="""
                sucursales_latitud  <> ''
                AND sucursales_longitud <> ''
                AND sucursales_latitud  ~ '^-?[0-9]+(\\.[0-9]+)?$'
                AND sucursales_longitud ~ '^-?[0-9]+(\\.[0-9]+)?$'
                AND NOT (
                    sucursales_latitud::numeric  BETWEEN %s AND %s
                    AND sucursales_longitud::numeric BETWEEN %s AND %s
                )
            """,
            params=(_ARG_LAT_MIN, _ARG_LAT_MAX, _ARG_LON_MIN, _ARG_LON_MAX),
            detail=lambda row: (
                "coordenadas fuera del bounding box de Argentina: "
                f"lat={row[3]} lon={row[4]}"
            ),
        )
    ]


def _reject_tipo_sucursal() -> list[_RejectionQuery]:
    return [
        _RejectionQuery(
            table="stg_sucursales",
            code="sucursal_tipo_invalid",
            columns=("id_comercio", "id_bandera", "id_sucursal", "sucursales_tipo"),
            condition="sucursales_tipo <> '' AND sucursales_tipo != ALL(%s)",
            params=(list(_VALID_TIPOS),),
            detail=lambda row: f"tipo de sucursal inválido: {row[3]!r}",
        )
    ]


def _reject_provincia() -> list[_RejectionQuery]:
    return [
        _RejectionQuery(
            table="stg_sucursales",
            code="provincia_invalid",
            columns=("id_comercio", "id_bandera", "id_sucursal", "sucursales_provincia"),
            condition="sucursales_provincia <> '' AND sucursales_provincia != ALL(%s)",
            params=(list(_VALID_PROVINCIAS),),
            detail=lambda row: f"provincia inválida: {row[3]!r}",
        )
    ]
