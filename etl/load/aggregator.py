"""
aggregator.py — Recalculate agg_precios_diarios for a processed day.

Strategy
--------
All aggregation is done in a single SQL statement that reads directly from
fact_precios (joined with dim_sucursal to get provincia_sk) and upserts into
agg_precios_diarios.  Only the rows for the given fecha_id are touched — the
rest of the table is left intact.

PostgreSQL's ``percentile_cont(0.5)`` computes the median inline, so no
data is transferred to Python.

Public API
----------
aggregate(conn, fecha_id)  → AggResult
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

log = structlog.get_logger(__name__)


@dataclass
class AggResult:
    fecha_id: int
    rows_upserted: int


_UPSERT_SQL = """
INSERT INTO agg_precios_diarios (
    producto_sk,
    fecha_id,
    provincia_sk,
    precio_lista_avg,
    precio_lista_min,
    precio_lista_max,
    precio_lista_median,
    precio_promo_avg,
    cantidad_obs,
    cantidad_con_promo,
    updated_at
)
SELECT
    f.producto_sk,
    f.fecha_id,
    s.provincia_sk,
    ROUND(AVG(f.precio_lista),                              2)  AS precio_lista_avg,
    MIN(f.precio_lista)                                         AS precio_lista_min,
    MAX(f.precio_lista)                                         AS precio_lista_max,
    ROUND(percentile_cont(0.5) WITHIN GROUP (
              ORDER BY f.precio_lista)::NUMERIC,             2) AS precio_lista_median,
    ROUND(AVG(
        LEAST(
            COALESCE(f.precio_promo1, f.precio_lista),
            COALESCE(f.precio_promo2, f.precio_lista)
        )
    ),                                                       2) AS precio_promo_avg,
    COUNT(*)                                                    AS cantidad_obs,
    COUNT(*) FILTER (WHERE f.tiene_promo)                       AS cantidad_con_promo,
    NOW()
FROM fact_precios   f
JOIN dim_sucursal   s ON s.sucursal_sk  = f.sucursal_sk
WHERE f.fecha_id        = %(fecha_id)s
  AND s.provincia_sk IS NOT NULL
GROUP BY f.producto_sk, f.fecha_id, s.provincia_sk
ON CONFLICT (producto_sk, fecha_id, provincia_sk) DO UPDATE SET
    precio_lista_avg    = EXCLUDED.precio_lista_avg,
    precio_lista_min    = EXCLUDED.precio_lista_min,
    precio_lista_max    = EXCLUDED.precio_lista_max,
    precio_lista_median = EXCLUDED.precio_lista_median,
    precio_promo_avg    = EXCLUDED.precio_promo_avg,
    cantidad_obs        = EXCLUDED.cantidad_obs,
    cantidad_con_promo  = EXCLUDED.cantidad_con_promo,
    updated_at          = EXCLUDED.updated_at
"""


def aggregate(conn, fecha_id: int) -> AggResult:
    """
    Recalculate ``agg_precios_diarios`` for *fecha_id*.

    Reads from ``fact_precios`` joined with ``dim_sucursal`` to resolve
    ``provincia_sk``.  Rows whose sucursal has no province assigned are
    excluded (they cannot be aggregated at provincial level).

    The operation is idempotent: re-running for the same fecha_id overwrites
    the existing aggregates with fresh values.

    Parameters
    ----------
    conn:
        Open psycopg2/psycopg3 connection (caller manages commit/rollback).
    fecha_id:
        Date in YYYYMMDD integer format (e.g. 20260315).

    Returns
    -------
    AggResult with the number of rows upserted.
    """
    log.info("aggregator.start", fecha_id=fecha_id)

    with conn.cursor() as cur:
        cur.execute(_UPSERT_SQL, {"fecha_id": fecha_id})
        rows = cur.rowcount

    log.info("aggregator.done", fecha_id=fecha_id, rows_upserted=rows)
    return AggResult(fecha_id=fecha_id, rows_upserted=rows)
