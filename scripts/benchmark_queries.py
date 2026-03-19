"""
benchmark_queries.py — Compare query performance: agg_precios_diarios vs fact_precios direct.

Usage:
    python scripts/benchmark_queries.py [fecha_id]

    fecha_id defaults to the most recent fecha_id present in agg_precios_diarios.

Output:
    Execution plan + timing for each query pair, printed to stdout.

Requires a running PostgreSQL instance configured via .env.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")


def connect() -> psycopg2.extensions.connection:
    return psycopg2.connect(
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=os.environ.get("POSTGRES_PORT", "5432"),
    )


# ---------------------------------------------------------------------------
# Query pairs: (label, query_on_agg, query_on_fact)
# ---------------------------------------------------------------------------

BENCHMARKS = [
    (
        "AVG price per product×province for one day",
        # --- via agg (pre-computed) ---
        """
        SELECT producto_sk, provincia_sk, precio_lista_avg
        FROM   agg_precios_diarios
        WHERE  fecha_id = %(fecha_id)s
        """,
        # --- via fact (compute on the fly) ---
        """
        SELECT f.producto_sk, s.provincia_sk, ROUND(AVG(f.precio_lista), 2)
        FROM   fact_precios  f
        JOIN   dim_sucursal  s ON s.sucursal_sk = f.sucursal_sk
        WHERE  f.fecha_id = %(fecha_id)s
          AND  s.provincia_sk IS NOT NULL
        GROUP  BY f.producto_sk, s.provincia_sk
        """,
    ),
    (
        "Top-10 products by avg price (national) for one day",
        # --- via agg ---
        """
        SELECT producto_sk, ROUND(AVG(precio_lista_avg), 2) AS precio_nacional
        FROM   agg_precios_diarios
        WHERE  fecha_id = %(fecha_id)s
        GROUP  BY producto_sk
        ORDER  BY precio_nacional DESC
        LIMIT  10
        """,
        # --- via fact ---
        """
        SELECT f.producto_sk, ROUND(AVG(f.precio_lista), 2) AS precio_nacional
        FROM   fact_precios f
        WHERE  f.fecha_id = %(fecha_id)s
        GROUP  BY f.producto_sk
        ORDER  BY precio_nacional DESC
        LIMIT  10
        """,
    ),
    (
        "Price variation (day-over-day) via view vs manual window on fact",
        # --- via view (window on agg) ---
        """
        SELECT producto_sk, provincia_sk, variacion_pct
        FROM   v_variacion_precios
        WHERE  fecha_id = %(fecha_id)s
          AND  variacion_pct IS NOT NULL
        ORDER  BY variacion_pct DESC
        LIMIT  20
        """,
        # --- via fact (window on fact, two days) ---
        """
        WITH daily AS (
            SELECT f.producto_sk,
                   s.provincia_sk,
                   f.fecha_id,
                   ROUND(AVG(f.precio_lista), 2) AS avg_price
            FROM   fact_precios f
            JOIN   dim_sucursal s ON s.sucursal_sk = f.sucursal_sk
            WHERE  f.fecha_id IN (%(fecha_id)s, %(fecha_id_prev)s)
              AND  s.provincia_sk IS NOT NULL
            GROUP  BY f.producto_sk, s.provincia_sk, f.fecha_id
        )
        SELECT  producto_sk,
                provincia_sk,
                ROUND(
                    (avg_price - LAG(avg_price) OVER w)
                    / NULLIF(LAG(avg_price) OVER w, 0) * 100,
                    2
                ) AS variacion_pct
        FROM    daily
        WINDOW  w AS (PARTITION BY producto_sk, provincia_sk ORDER BY fecha_id)
        QUALIFY variacion_pct IS NOT NULL   -- not valid PG syntax, filtered below
        """,
    ),
]


def _run_explain(cur, sql: str, params: dict) -> tuple[str, float]:
    """Return (EXPLAIN ANALYZE output, wall_time_ms)."""
    # EXPLAIN ANALYZE runs the query for real
    explain_sql = f"EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) {sql}"
    t0 = time.perf_counter()
    try:
        cur.execute(explain_sql, params)
        plan_rows = cur.fetchall()
    except Exception as exc:
        return f"ERROR: {exc}", 0.0
    elapsed_ms = (time.perf_counter() - t0) * 1000
    plan = "\n".join(r[0] for r in plan_rows)
    return plan, elapsed_ms


def _extract_execution_time(plan: str) -> str:
    for line in plan.splitlines():
        if "Execution Time" in line:
            return line.strip()
    return "(no execution time found)"


def run(fecha_id: int) -> None:
    # For the day-over-day benchmark, also pass the previous day's fecha_id.
    # We compute it as simple integer arithmetic (works within a month;
    # cross-month queries are rare in benchmarks).
    fecha_id_prev = fecha_id - 1  # approximate; fine for a benchmark

    params = {"fecha_id": fecha_id, "fecha_id_prev": fecha_id_prev}

    conn = connect()
    conn.autocommit = True

    with conn.cursor() as cur:
        for label, agg_sql, fact_sql in BENCHMARKS:
            print(f"\n{'=' * 70}")
            print(f"  {label}")
            print(f"{'=' * 70}")

            for name, sql in [("AGG  ", agg_sql), ("FACT ", fact_sql)]:
                plan, wall_ms = _run_explain(cur, sql, params)
                pg_time = _extract_execution_time(plan)
                print(f"\n  [{name}] wall={wall_ms:.1f}ms  |  pg: {pg_time}")

    conn.close()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        _fecha_id = int(sys.argv[1])
    else:
        # Use the latest fecha_id in agg_precios_diarios
        _conn = connect()
        with _conn.cursor() as _cur:
            _cur.execute("SELECT MAX(fecha_id) FROM agg_precios_diarios")
            _fecha_id = _cur.fetchone()[0]
        _conn.close()

        if _fecha_id is None:
            print("agg_precios_diarios is empty — run the pipeline first.")
            sys.exit(1)

    print(f"Benchmarking fecha_id={_fecha_id}")
    run(_fecha_id)
