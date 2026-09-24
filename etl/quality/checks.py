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

import psycopg2
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

# Maximum allowed relative drift in productos row count vs previous run
DEFAULT_MAX_DRIFT = 0.20  # 20 %

# Argentina bounding box (approximate, covers continental + Malvinas)
_ARG_LAT_MIN = -55.1
_ARG_LAT_MAX = -21.8
_ARG_LON_MIN = -73.6
_ARG_LON_MAX = -53.6

# Valid sucursales_tipo values (must match TipoSucursal in models/sucursal.py)
_VALID_TIPOS = {"Hipermercado", "Supermercado", "Autoservicio", "Tradicional", "Web"}

# Known Argentine province ISO 3166-2 codes
_VALID_PROVINCIAS = {
    "AR-B",  # Buenos Aires
    "AR-C",  # Ciudad Autónoma de Buenos Aires
    "AR-K",  # Catamarca
    "AR-H",  # Chaco
    "AR-U",  # Chubut
    "AR-X",  # Córdoba
    "AR-W",  # Corrientes
    "AR-E",  # Entre Ríos
    "AR-P",  # Formosa
    "AR-Y",  # Jujuy
    "AR-L",  # La Pampa
    "AR-F",  # La Rioja
    "AR-M",  # Mendoza
    "AR-N",  # Misiones
    "AR-Q",  # Neuquén
    "AR-R",  # Río Negro
    "AR-A",  # Salta
    "AR-J",  # San Juan
    "AR-D",  # San Luis
    "AR-Z",  # Santa Cruz
    "AR-S",  # Santa Fe
    "AR-G",  # Santiago del Estero
    "AR-V",  # Tierra del Fuego
    "AR-T",  # Tucumán
}

# Required (non-nullable) columns per table — empty string counts as null
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
    max_drift: float = DEFAULT_MAX_DRIFT,
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
    max_drift:
        Maximum allowed relative change in productos count vs the previous
        completed run (0.20 = 20 %).

    Returns
    -------
    list[CheckResult] — one entry per check, in order.
    """
    logger = log.bind(run_id=run_id)
    logger.info("quality.checks.start")
    product_rows = _product_row_count(conn, run_id)

    results: list[CheckResult] = [
        check_schema(conn, run_id),
        check_pk_uniqueness(conn, run_id),
        check_fk_integrity(conn, run_id),
        check_positive_prices(conn, run_id),
        check_min_row_count(
            conn, run_id, min_productos=min_productos, current_rows=product_rows
        ),
        # Warning checks
        check_row_count_drift(conn, run_id, max_drift=max_drift, current_rows=product_rows),
        check_required_nulls(conn, run_id),
        check_coordinates_bbox(conn, run_id),
        check_tipo_sucursal(conn, run_id),
        check_provincia(conn, run_id),
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
            SELECT COALESCE(SUM(row_count - 1), 0)
              FROM (
                  SELECT COUNT(*) AS row_count
                    FROM stg_productos
                   WHERE run_id = %s
                   GROUP BY id_comercio, id_bandera, id_sucursal, id_producto
                  HAVING COUNT(*) > 1
              ) AS duplicate_keys
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
        severity=Severity.WARNING,
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
        cur.execute(
            """
            WITH comercios AS (
                SELECT DISTINCT id_comercio, id_bandera
                  FROM stg_comercios
                 WHERE run_id = %s
            ), sucursales AS (
                SELECT DISTINCT id_comercio, id_bandera, id_sucursal
                  FROM stg_sucursales
                 WHERE run_id = %s
            )
            SELECT
                COUNT(*) FILTER (WHERE c.id_comercio IS NULL),
                COUNT(*) FILTER (WHERE s.id_comercio IS NULL)
              FROM stg_productos p
              LEFT JOIN comercios c
                ON c.id_comercio = p.id_comercio
               AND c.id_bandera = p.id_bandera
              LEFT JOIN sucursales s
                ON s.id_comercio = p.id_comercio
               AND s.id_bandera = p.id_bandera
               AND s.id_sucursal = p.id_sucursal
             WHERE p.run_id = %s
            """,
            (run_id, run_id, run_id),
        )
        missing_comercios, missing_sucursales = cur.fetchone()
        if missing_comercios:
            violations["productos_sin_comercio"] = missing_comercios
        if missing_sucursales:
            violations["productos_sin_sucursal"] = missing_sucursales

    return CheckResult(
        name="fk_integrity",
        passed=not violations,
        severity=Severity.WARNING,
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
            SELECT
                COUNT(*) FILTER (
                    WHERE productos_precio_lista = ''
                       OR productos_precio_lista !~ '^[0-9]+(\\.[0-9]+)?$'
                       OR productos_precio_lista::numeric <= 0
                ),
                COUNT(*)
              FROM stg_productos
             WHERE run_id = %s
            """,
            (run_id,),
        )
        bad_count, total = cur.fetchone()

    return CheckResult(
        name="positive_prices",
        passed=bad_count == 0,
        severity=Severity.WARNING,
        details={"bad_precio_lista_rows": bad_count, "total_rows": total},
    )


def _product_row_count(conn: "psycopg2.connection", run_id: int) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM stg_productos WHERE run_id = %s", (run_id,))
        return cur.fetchone()[0]


def check_min_row_count(
    conn: "psycopg2.connection",
    run_id: int,
    *,
    min_productos: int = DEFAULT_MIN_PRODUCTOS,
    current_rows: int | None = None,
) -> CheckResult:
    """
    Verify that stg_productos has at least *min_productos* rows for *run_id*.

    A very low row count is a strong indicator that the source ZIP was
    truncated or corrupted during download.  Severity: CRITICAL.
    """
    actual = current_rows if current_rows is not None else _product_row_count(conn, run_id)

    return CheckResult(
        name="min_row_count",
        passed=actual >= min_productos,
        severity=Severity.CRITICAL,
        details={"actual_rows": actual, "min_required": min_productos},
    )


# ── Warning checks ─────────────────────────────────────────────────────────────


def check_row_count_drift(
    conn: "psycopg2.connection",
    run_id: int,
    *,
    max_drift: float = DEFAULT_MAX_DRIFT,
    current_rows: int | None = None,
) -> CheckResult:
    """
    Compare productos row count vs the previous completed run.

    A drift greater than *max_drift* (default 20 %) suggests an unusual
    data volume change (missing chains, truncated ZIP, or data explosion).
    If there is no prior completed run the check is skipped (passed=True).
    Severity: WARNING.
    """
    with conn.cursor() as cur:
        current = current_rows if current_rows is not None else _product_row_count(conn, run_id)

        cur.execute(
            """
            SELECT ir.rows_productos_raw
              FROM ingestion_runs ir
             WHERE ir.id < %s
               AND ir.status = 'completed'
               AND ir.is_partial = FALSE
               AND ir.rows_productos_raw IS NOT NULL
             ORDER BY ir.id DESC
             LIMIT 1
            """,
            (run_id,),
        )
        row = cur.fetchone()

    if row is None:
        return CheckResult(
            name="row_count_drift",
            passed=True,
            severity=Severity.WARNING,
            details={"current_rows": current, "previous_rows": None, "drift": None},
        )

    previous = row[0]
    if previous == 0:
        drift = 0.0
    else:
        drift = abs(current - previous) / previous

    return CheckResult(
        name="row_count_drift",
        passed=drift <= max_drift,
        severity=Severity.WARNING,
        details={
            "current_rows": current,
            "previous_rows": previous,
            "drift": round(drift, 4),
            "max_drift": max_drift,
        },
    )


def check_required_nulls(
    conn: "psycopg2.connection",
    run_id: int,
) -> CheckResult:
    """
    Count rows with empty values in required (non-nullable) columns.

    Because staging stores everything as VARCHAR, both SQL NULL and the
    empty string '' are treated as missing.  Severity: WARNING.
    """
    table_checks = [
        ("stg_comercios", _REQUIRED_COMERCIOS),
        ("stg_sucursales", _REQUIRED_SUCURSALES),
        ("stg_productos", _REQUIRED_PRODUCTOS),
    ]

    nulls_by_table: dict[str, dict[str, int]] = {}

    with conn.cursor() as cur:
        for table, required_cols in table_checks:
            counts_sql = ", ".join(
                f"COUNT(*) FILTER (WHERE TRIM({col}) = '' OR {col} IS NULL)"
                for col in required_cols
            )
            cur.execute(
                f"""
                SELECT {counts_sql}
                  FROM {table}
                 WHERE run_id = %s
                """,
                (run_id,),
            )
            counts = cur.fetchone()
            col_nulls = {
                column: count
                for column, count in zip(required_cols, counts, strict=True)
                if count
            }
            if col_nulls:
                nulls_by_table[table] = col_nulls

    return CheckResult(
        name="required_nulls",
        passed=not nulls_by_table,
        severity=Severity.WARNING,
        details={"nulls_by_table": nulls_by_table},
    )


def check_coordinates_bbox(
    conn: "psycopg2.connection",
    run_id: int,
) -> CheckResult:
    """
    Verify that non-empty coordinates in stg_sucursales fall within
    Argentina's bounding box.

    Rows with empty or non-numeric coordinates are skipped (they are
    caught by check_required_nulls if the field is required).
    Severity: WARNING.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*)
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
        out_of_bbox = cur.fetchone()[0]

        cur.execute(
            """
            SELECT COUNT(*)
              FROM stg_sucursales
             WHERE run_id = %s
               AND sucursales_latitud  <> ''
               AND sucursales_longitud <> ''
            """,
            (run_id,),
        )
        total_with_coords = cur.fetchone()[0]

    return CheckResult(
        name="coordinates_bbox",
        passed=out_of_bbox == 0,
        severity=Severity.WARNING,
        details={
            "out_of_bbox": out_of_bbox,
            "total_with_coords": total_with_coords,
            "bbox": {
                "lat": [_ARG_LAT_MIN, _ARG_LAT_MAX],
                "lon": [_ARG_LON_MIN, _ARG_LON_MAX],
            },
        },
    )


def check_tipo_sucursal(
    conn: "psycopg2.connection",
    run_id: int,
) -> CheckResult:
    """
    Verify that sucursales_tipo contains only known valid values.

    Valid types: Hipermercado, Supermercado, Autoservicio, Tradicional, Web.
    Severity: WARNING.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT sucursales_tipo, COUNT(*) AS cnt
              FROM stg_sucursales
             WHERE run_id = %s
               AND sucursales_tipo <> ''
               AND sucursales_tipo != ALL(%s)
             GROUP BY sucursales_tipo
             ORDER BY cnt DESC
            """,
            (run_id, list(_VALID_TIPOS)),
        )
        invalid_rows = {row[0]: row[1] for row in cur.fetchall()}

    return CheckResult(
        name="tipo_sucursal",
        passed=not invalid_rows,
        severity=Severity.WARNING,
        details={"invalid_tipos": invalid_rows, "valid_tipos": sorted(_VALID_TIPOS)},
    )


def check_provincia(
    conn: "psycopg2.connection",
    run_id: int,
) -> CheckResult:
    """
    Verify that sucursales_provincia contains only known AR-* ISO 3166-2 codes.

    Severity: WARNING.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT sucursales_provincia, COUNT(*) AS cnt
              FROM stg_sucursales
             WHERE run_id = %s
               AND sucursales_provincia <> ''
               AND sucursales_provincia != ALL(%s)
             GROUP BY sucursales_provincia
             ORDER BY cnt DESC
            """,
            (run_id, list(_VALID_PROVINCIAS)),
        )
        invalid_rows = {row[0]: row[1] for row in cur.fetchall()}

    return CheckResult(
        name="provincia",
        passed=not invalid_rows,
        severity=Severity.WARNING,
        details={"invalid_provincias": invalid_rows},
    )
