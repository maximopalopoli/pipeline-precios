"""Health distinguishes visible prices from newer ingestion attempts."""

import asyncio
from datetime import date

from api.routes import health
from etl.load.warehouse_loader import _ensure_partition


def _insert_run(conn, *, name: str, day: date, status: str, started: str,
                finished: str | None, partial: bool = False, published: int = 0) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO raw_files (filename, url, sha256, size_bytes, fecha_datos)
            VALUES (%s, 'https://example.test/data.zip', %s, 1, %s)
            RETURNING id
            """,
            (name, name, day),
        )
        raw_id = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO ingestion_runs (
                raw_file_id, status, started_at, finished_at,
                is_partial, rows_fact_published
            ) VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (raw_id, status, started, finished, partial, published),
        )
        return cur.fetchone()[0]


def _insert_visible_fact(
    conn, *, run_id: int, day: date, producto_sk: int, sucursal_sk: int
) -> None:
    fecha_id = int(day.strftime("%Y%m%d"))
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO dim_fecha (
                fecha_id, fecha, anio, mes, trimestre, semana_iso,
                dia_semana, es_fin_semana
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, FALSE)
            """,
            (fecha_id, day, day.year, day.month, 1,
             day.isocalendar().week, day.isoweekday()),
        )
        cur.execute(
            """
            INSERT INTO fact_precios (
                producto_sk, sucursal_sk, fecha_id, run_id, precio_lista
            ) VALUES (%s, %s, %s, %s, 100)
            """,
            (producto_sk, sucursal_sk, fecha_id, run_id),
        )


def test_health_warns_after_failed_full_run_and_clears_after_publication(
    pg_conn, monkeypatch
):
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO dim_provincia (codigo_iso, nombre, region) "
            "VALUES ('AR-C', 'CABA', 'AMBA') RETURNING provincia_sk"
        )
        provincia_sk = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO dim_comercio (id_comercio, id_bandera) "
            "VALUES (1, 1) RETURNING comercio_sk"
        )
        comercio_sk = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO dim_sucursal (
                id_comercio, id_bandera, id_sucursal, comercio_sk, provincia_sk
            ) VALUES (1, 1, 1, %s, %s)
            RETURNING sucursal_sk
            """,
            (comercio_sk, provincia_sk),
        )
        sucursal_sk = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO dim_producto (producto_identity, id_producto) "
            "VALUES ('test:1', '1') RETURNING producto_sk"
        )
        producto_sk = cur.fetchone()[0]
    _ensure_partition(pg_conn, 20260317)
    _ensure_partition(pg_conn, 20260318)

    first = _insert_run(
        pg_conn, name="published", day=date(2026, 3, 17),
        status="completed", started="2026-03-17 09:00:00+00",
        finished="2026-03-17 09:30:00+00", published=1,
    )
    _insert_visible_fact(
        pg_conn, run_id=first, day=date(2026, 3, 17),
        producto_sk=producto_sk, sucursal_sk=sucursal_sk,
    )
    _insert_run(
        pg_conn, name="failed", day=date(2026, 3, 18),
        status="failed", started="2026-03-18 09:00:00+00",
        finished="2026-03-18 09:10:00+00",
    )
    _insert_run(
        pg_conn, name="partial", day=date(2026, 3, 19),
        status="completed", started="2026-03-19 09:00:00+00",
        finished="2026-03-19 09:10:00+00", partial=True,
    )
    pg_conn.commit()

    async def fetchrow(sql: str):
        with pg_conn.cursor() as cur:
            cur.execute(sql)
            columns = [column.name for column in cur.description]
            value = cur.fetchone()
            return dict(zip(columns, value)) if value else None

    monkeypatch.setattr(health, "fetchrow", fetchrow)
    before = asyncio.run(health.get_health())
    assert before["is_partial"] is True
    assert before["ultima_publicacion"]["fecha_datos"] == "2026-03-17"
    assert before["aviso_ingesta"]["status"] == "failed"
    assert before["aviso_ingesta"]["fecha_datos"] == "2026-03-18"

    second = _insert_run(
        pg_conn, name="recovered", day=date(2026, 3, 18),
        status="completed", started="2026-03-20 09:00:00+00",
        finished="2026-03-20 09:30:00+00", published=1,
    )
    _insert_visible_fact(
        pg_conn, run_id=second, day=date(2026, 3, 18),
        producto_sk=producto_sk, sucursal_sk=sucursal_sk,
    )
    pg_conn.commit()

    after = asyncio.run(health.get_health())
    assert after["ultima_publicacion"]["fecha_datos"] == "2026-03-18"
    assert after["aviso_ingesta"] is None
