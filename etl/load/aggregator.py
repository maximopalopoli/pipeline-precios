"""
aggregator.py — Recalculate agg_precios_diarios for a processed day.

Strategy
--------
All aggregation is done in a single transaction managed by the caller.  The
existing aggregate set for the requested day is removed, then rebuilt from
fact_precios (joined with dim_sucursal to get provincia_sk).  Only rows for
the given fecha_id are touched.

PostgreSQL's ``percentile_cont(0.5)`` computes the median inline, so no
data is transferred to Python.

Public API
----------
aggregate(conn, fecha_id)  → AggResult
"""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter

import structlog

log = structlog.get_logger(__name__)


@dataclass
class AggResult:
    fecha_id: int
    rows_upserted: int
    delete_seconds: float
    insert_seconds: float


_DELETE_SQL = "DELETE FROM agg_precios_diarios WHERE fecha_id = %(fecha_id)s"

_INSERT_SQL = """
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
    ROUND(AVG(pipeline_effective_price(
        f.precio_lista, f.precio_promo1, f.precio_promo2
    )),                                                      2) AS precio_promo_avg,
    COUNT(*)                                                    AS cantidad_obs,
    COUNT(*) FILTER (WHERE f.tiene_promo)                       AS cantidad_con_promo,
    NOW()
FROM fact_precios   f
JOIN dim_sucursal   s ON s.sucursal_sk  = f.sucursal_sk
WHERE f.fecha_id        = %(fecha_id)s
  AND s.provincia_sk IS NOT NULL
GROUP BY f.producto_sk, f.fecha_id, s.provincia_sk
"""


def aggregate(conn, fecha_id: int) -> AggResult:
    """
    Recalculate ``agg_precios_diarios`` for *fecha_id*.

    Reads from ``fact_precios`` joined with ``dim_sucursal`` to resolve
    ``provincia_sk``.  Rows whose sucursal has no province assigned are
    excluded (they cannot be aggregated at provincial level).

    The operation replaces the full aggregate set for the day, including the
    removal of groups absent from a rectified source file.  The caller must
    commit or roll back the delete and insert together.

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
        started_at = perf_counter()
        cur.execute(_DELETE_SQL, {"fecha_id": fecha_id})
        delete_seconds = perf_counter() - started_at
        started_at = perf_counter()
        cur.execute(_INSERT_SQL, {"fecha_id": fecha_id})
        rows = cur.rowcount
        insert_seconds = perf_counter() - started_at

    log.info(
        "aggregator.done",
        fecha_id=fecha_id,
        rows_upserted=rows,
        delete_seconds=round(delete_seconds, 3),
        insert_seconds=round(insert_seconds, 3),
    )
    return AggResult(fecha_id, rows, delete_seconds, insert_seconds)
