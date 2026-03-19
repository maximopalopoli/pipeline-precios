"""
checks.py — Critical quality checks for SEPA staging data.

All checks query the stg_* tables for a given run_id.  Results are
returned as CheckResult objects so callers can inspect or log them.

Severity:
  - CRITICAL: run_all() raises QualityCheckError after collecting all
    results if any critical check fails (fail-fast is intentional so
    the caller sees every failure in one pass).
  - WARNING: logged and included in results but does not halt execution.

Usage::

    from etl.quality.checks import run_all, QualityCheckError

    try:
        results = run_all(conn, run_id)
    except QualityCheckError as exc:
        log.error("quality.failed", failures=exc.failures)
        raise
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import structlog

log = structlog.get_logger(__name__)

# ── Expected column sets (must stay in sync with loader.py and migrations) ─────

_EXPECTED_COMERCIOS_COLS = {
    "run_id",
    "id_comercio",
    "id_bandera",
    "comercio_cuit",
    "comercio_razon_social",
    "comercio_bandera_nombre",
    "comercio_bandera_url",
    "comercio_ultima_actualizacion",
    "comercio_version_sepa",
}

_EXPECTED_SUCURSALES_COLS = {
    "run_id",
    "id_comercio",
    "id_bandera",
    "id_sucursal",
    "sucursales_nombre",
    "sucursales_tipo",
    "sucursales_calle",
    "sucursales_numero",
    "sucursales_latitud",
    "sucursales_longitud",
    "sucursales_observaciones",
    "sucursales_barrio",
    "sucursales_codigo_postal",
    "sucursales_localidad",
    "sucursales_provincia",
    "sucursales_lunes_horario_atencion",
    "sucursales_martes_horario_atencion",
    "sucursales_miercoles_horario_atencion",
    "sucursales_jueves_horario_atencion",
    "sucursales_viernes_horario_atencion",
    "sucursales_sabado_horario_atencion",
    "sucursales_domingo_horario_atencion",
}

_EXPECTED_PRODUCTOS_COLS = {
    "run_id",
    "id_comercio",
    "id_bandera",
    "id_sucursal",
    "id_producto",
    "productos_ean",
    "productos_descripcion",
    "productos_cantidad_presentacion",
    "productos_unidad_medida_presentacion",
    "productos_marca",
    "productos_precio_lista",
    "productos_precio_referencia",
    "productos_cantidad_referencia",
    "productos_unidad_medida_referencia",
    "productos_precio_unitario_promo1",
    "productos_leyenda_promo1",
    "productos_precio_unitario_promo2",
    "productos_leyenda_promo2",
}

# Minimum number of product rows expected per run (protects against truncated ZIPs)
DEFAULT_MIN_PRODUCTOS = 100


# ── Result types ───────────────────────────────────────────────────────────────


class Severity(str, Enum):
    CRITICAL = "CRITICAL"
    WARNING = "WARNING"


@dataclass
class CheckResult:
    name: str
    passed: bool
    severity: Severity
    details: dict[str, Any] = field(default_factory=dict)


class QualityCheckError(Exception):
    """Raised by run_all() when one or more CRITICAL checks fail."""

    def __init__(self, failures: list[CheckResult]) -> None:
        self.failures = failures
        names = [r.name for r in failures]
        super().__init__(f"Quality checks failed: {names}")


# ── Public API ─────────────────────────────────────────────────────────────────


def run_all(
    conn: "psycopg2.connection",
    run_id: int,
    *,
    min_productos: int = DEFAULT_MIN_PRODUCTOS,
) -> list[CheckResult]:
    """
    Run all quality checks for *run_id* against the stg_* tables.

    Collects every check result before raising so the caller sees all
    failures in one pass.  Raises QualityCheckError if any CRITICAL
    check fails.

    Parameters
    ----------
    conn:
        Open psycopg2 connection (read-only — no mutations performed).
    run_id:
        ingestion_runs.id whose staging rows are being validated.
    min_productos:
        Minimum number of productos rows required to pass the row-count check.

    Returns
    -------
    list[CheckResult] — one entry per check, in order.
    """
    logger = log.bind(run_id=run_id)
    logger.info("quality.checks.start")

    results: list[CheckResult] = [
        check_schema(conn, run_id),
        check_pk_uniqueness(conn, run_id),
        check_fk_integrity(conn, run_id),
        check_positive_prices(conn, run_id),
        check_min_row_count(conn, run_id, min_productos=min_productos),
    ]

    for r in results:
        level = "quality.check.passed" if r.passed else "quality.check.failed"
        logger.bind(check=r.name, severity=r.severity, **r.details).info(level)

    critical_failures = [r for r in results if not r.passed and r.severity == Severity.CRITICAL]
    if critical_failures:
        raise QualityCheckError(critical_failures)

    logger.info("quality.checks.done", total=len(results))
    return results


# ── Individual checks ──────────────────────────────────────────────────────────


def check_schema(
    conn: "psycopg2.connection",
    run_id: int,
) -> CheckResult:
    """
    Verify that the stg_* tables have exactly the expected columns.

    Detects migrations that added/removed columns without updating the
    loader or vice-versa.  Severity: CRITICAL.
    """
    table_expectations = {
        "stg_comercios": _EXPECTED_COMERCIOS_COLS,
        "stg_sucursales": _EXPECTED_SUCURSALES_COLS,
        "stg_productos": _EXPECTED_PRODUCTOS_COLS,
    }

    missing_by_table: dict[str, list[str]] = {}
    extra_by_table: dict[str, list[str]] = {}

    with conn.cursor() as cur:
        for table, expected in table_expectations.items():
            cur.execute(
                """
                SELECT column_name
                  FROM information_schema.columns
                 WHERE table_name = %s
                """,
                (table,),
            )
            actual = {row[0] for row in cur.fetchall()}
            missing = sorted(expected - actual)
            extra = sorted(actual - expected)
            if missing:
                missing_by_table[table] = missing
            if extra:
                extra_by_table[table] = extra

    passed = not missing_by_table and not extra_by_table
    return CheckResult(
        name="schema_match",
        passed=passed,
        severity=Severity.CRITICAL,
        details={"missing_cols": missing_by_table, "extra_cols": extra_by_table},
    )


def check_pk_uniqueness(
    conn: "psycopg2.connection",
    run_id: int,
) -> CheckResult:
    """
    Verify that PKs are unique within each staging table for *run_id*.

    Primary keys:
      - stg_comercios:  (id_comercio, id_bandera)
      - stg_sucursales: (id_comercio, id_bandera, id_sucursal)
      - stg_productos:  (id_comercio, id_bandera, id_sucursal, id_producto)

    Severity: CRITICAL.
    """
    queries = {
        "stg_comercios": """
            SELECT COUNT(*) - COUNT(DISTINCT (id_comercio, id_bandera))
              FROM stg_comercios
             WHERE run_id = %s
        """,
        "stg_sucursales": """
            SELECT COUNT(*) - COUNT(DISTINCT (id_comercio, id_bandera, id_sucursal))
              FROM stg_sucursales
             WHERE run_id = %s
        """,
        "stg_productos": """
            SELECT COUNT(*) - COUNT(DISTINCT (id_comercio, id_bandera, id_sucursal, id_producto))
              FROM stg_productos
             WHERE run_id = %s
        """,
    }

    duplicates: dict[str, int] = {}
    with conn.cursor() as cur:
        for table, sql in queries.items():
            cur.execute(sql, (run_id,))
            dup_count = cur.fetchone()[0]
            if dup_count > 0:
                duplicates[table] = dup_count

    return CheckResult(
        name="pk_uniqueness",
        passed=not duplicates,
        severity=Severity.CRITICAL,
        details={"duplicate_rows": duplicates},
    )


def check_fk_integrity(
    conn: "psycopg2.connection",
    run_id: int,
) -> CheckResult:
    """
    Verify referential integrity across staging tables for *run_id*.

    Rules:
      - Every (id_comercio, id_bandera) in stg_productos must exist in
        stg_comercios (same run_id).
      - Every (id_comercio, id_bandera, id_sucursal) in stg_productos
        must exist in stg_sucursales (same run_id).

    Severity: CRITICAL.
    """
    violations: dict[str, int] = {}

    with conn.cursor() as cur:
        # productos → comercio
        cur.execute(
            """
            SELECT COUNT(*)
              FROM stg_productos p
             WHERE p.run_id = %s
               AND NOT EXISTS (
                   SELECT 1
                     FROM stg_comercios c
                    WHERE c.run_id = p.run_id
                      AND c.id_comercio = p.id_comercio
                      AND c.id_bandera  = p.id_bandera
               )
            """,
            (run_id,),
        )
        count = cur.fetchone()[0]
        if count:
            violations["productos_sin_comercio"] = count

        # productos → sucursales
        cur.execute(
            """
            SELECT COUNT(*)
              FROM stg_productos p
             WHERE p.run_id = %s
               AND NOT EXISTS (
                   SELECT 1
                     FROM stg_sucursales s
                    WHERE s.run_id     = p.run_id
                      AND s.id_comercio = p.id_comercio
                      AND s.id_bandera  = p.id_bandera
                      AND s.id_sucursal = p.id_sucursal
               )
            """,
            (run_id,),
        )
        count = cur.fetchone()[0]
        if count:
            violations["productos_sin_sucursal"] = count

    return CheckResult(
        name="fk_integrity",
        passed=not violations,
        severity=Severity.CRITICAL,
        details={"violations": violations},
    )


def check_positive_prices(
    conn: "psycopg2.connection",
    run_id: int,
) -> CheckResult:
    """
    Verify that productos_precio_lista is a positive number for all rows.

    Rows with empty, non-numeric, zero, or negative precio_lista are counted
    as failures.  Severity: WARNING (bad-price rows are rejected later by the
    transform stage; a non-zero count here does not halt the pipeline).
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*)
              FROM stg_productos
             WHERE run_id = %s
               AND (
                   productos_precio_lista = ''
                   OR productos_precio_lista !~ '^[0-9]+(\.[0-9]+)?$'
                   OR productos_precio_lista::numeric <= 0
               )
            """,
            (run_id,),
        )
        bad_count = cur.fetchone()[0]

        cur.execute(
            "SELECT COUNT(*) FROM stg_productos WHERE run_id = %s",
            (run_id,),
        )
        total = cur.fetchone()[0]

    return CheckResult(
        name="positive_prices",
        passed=bad_count == 0,
        severity=Severity.WARNING,
        details={"bad_precio_lista_rows": bad_count, "total_rows": total},
    )


def check_min_row_count(
    conn: "psycopg2.connection",
    run_id: int,
    *,
    min_productos: int = DEFAULT_MIN_PRODUCTOS,
) -> CheckResult:
    """
    Verify that stg_productos has at least *min_productos* rows for *run_id*.

    A very low row count is a strong indicator that the source ZIP was
    truncated or corrupted during download.  Severity: CRITICAL.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM stg_productos WHERE run_id = %s",
            (run_id,),
        )
        actual = cur.fetchone()[0]

    return CheckResult(
        name="min_row_count",
        passed=actual >= min_productos,
        severity=Severity.CRITICAL,
        details={"actual_rows": actual, "min_required": min_productos},
    )
