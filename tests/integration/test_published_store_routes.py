"""Store discovery uses the latest published prices, not retained dimensions."""

import asyncio
import re
from datetime import date

from api.routes import filters, kpis, locales
from etl.load.warehouse_loader import _ensure_partition


def _seed_day(conn, value: date, *, store_name: str, province: str, chain: str) -> int:
    fecha_id = int(value.strftime("%Y%m%d"))
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO dim_fecha (
                fecha_id, fecha, anio, mes, trimestre, semana_iso,
                dia_semana, es_fin_semana
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                fecha_id, value, value.year, value.month,
                (value.month - 1) // 3 + 1, value.isocalendar().week,
                value.isoweekday(), value.isoweekday() >= 6,
            ),
        )
        cur.execute(
            """
            INSERT INTO raw_files (filename, url, sha256, size_bytes, fecha_datos)
            VALUES (%s, 'https://example.test/sepa.zip', %s, 1, %s)
            RETURNING id
            """,
            (store_name, store_name, value),
        )
        raw_file_id = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO ingestion_runs (raw_file_id, status, rows_fact_published)
            VALUES (%s, 'completed', 1)
            RETURNING id
            """,
            (raw_file_id,),
        )
        run_id = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO dim_comercio (id_comercio, id_bandera, comercio_bandera_nombre)
            VALUES (%s, 1, %s)
            RETURNING comercio_sk
            """,
            (run_id, chain),
        )
        comercio_sk = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO dim_sucursal (
                id_comercio, id_bandera, id_sucursal, comercio_sk, provincia_sk,
                sucursales_nombre, sucursales_localidad,
                sucursales_latitud, sucursales_longitud
            ) VALUES (
                %s, 1, 1, %s,
                (SELECT provincia_sk FROM dim_provincia WHERE nombre = %s),
                %s, %s, -34.603700, -58.381600
            ) RETURNING sucursal_sk
            """,
            (run_id, comercio_sk, province, store_name, store_name),
        )
        sucursal_sk = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO dim_producto (producto_identity, id_producto)
            VALUES (%s, %s)
            RETURNING producto_sk
            """,
            (f"test:{run_id}", str(run_id)),
        )
        producto_sk = cur.fetchone()[0]
    _ensure_partition(conn, fecha_id)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO fact_precios (
                producto_sk, sucursal_sk, fecha_id, run_id, precio_lista
            ) VALUES (%s, %s, %s, %s, 100)
            """,
            (producto_sk, sucursal_sk, fecha_id, run_id),
        )
    return sucursal_sk


def _fetch(conn, sql, *params):
    sql = re.sub(r"\$\d+", "%s", sql)
    with conn.cursor() as cur:
        cur.execute(sql, params)
        columns = [col[0] for col in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


def test_store_routes_exclude_stores_only_in_retained_older_day(pg_conn, monkeypatch):
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO dim_provincia (codigo_iso, nombre, region)
            VALUES ('AR-B', 'Buenos Aires', 'Pampeana'),
                   ('AR-C', 'CABA', 'AMBA')
            """
        )
    _seed_day(
        pg_conn, date(2026, 3, 17), store_name="Old store",
        province="Buenos Aires", chain="Old chain",
    )
    current_store = _seed_day(
        pg_conn, date(2026, 3, 18), store_name="Current store",
        province="CABA", chain="Current chain",
    )
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO raw_files (filename, url, sha256, size_bytes, fecha_datos)
            VALUES ('failed.zip', 'https://example.test/failed.zip', 'failed', 1, '2026-03-19'),
                   ('partial.zip', 'https://example.test/partial.zip', 'partial', 1, '2026-03-20')
            """
        )
        cur.execute(
            """
            INSERT INTO ingestion_runs (raw_file_id, status, rows_fact_published)
            SELECT id, CASE WHEN filename = 'failed.zip' THEN 'failed' ELSE 'completed' END, 0
            FROM raw_files
            WHERE filename IN ('failed.zip', 'partial.zip')
            """
        )
    pg_conn.commit()

    async def fetch(sql, *params):
        return _fetch(pg_conn, sql, *params)

    async def fetchrow(sql, *params):
        rows = _fetch(pg_conn, sql, *params)
        return rows[0] if rows else None

    monkeypatch.setattr(filters, "fetch", fetch)
    monkeypatch.setattr(locales, "fetch", fetch)

    assert asyncio.run(filters.get_provinces()) == ["CABA"]
    assert asyncio.run(filters.get_chains()) == ["Current chain"]
    assert asyncio.run(filters.get_localities("Buenos Aires")) == []
    assert asyncio.run(filters.get_localities("CABA")) == ["Current store"]

    stores = asyncio.run(
        locales.get_locales(-34.6037, -58.3816, radius=5, cadenas=None)
    )
    assert [store["sucursal_sk"] for store in stores] == [current_store]

    async def kpi_fetchrow(sql, *params):
        if "COUNT(*) AS n FROM dim_sucursal" in sql:
            return await fetchrow(sql, *params)
        return None

    async def no_rows(sql, *params):
        return []

    monkeypatch.setattr(kpis, "fetchrow", kpi_fetchrow)
    monkeypatch.setattr(kpis, "fetch", no_rows)
    assert asyncio.run(kpis.get_kpis())["total_sucursales"] == 1
