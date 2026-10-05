"""Shopping routes read published facts and run metadata only."""

import asyncio
import re
from datetime import date
from decimal import Decimal

from api.routes import buscador, locales, ofertas
from etl.load.warehouse_loader import _ensure_partition


def test_shopper_dates_and_promotions_read_published_facts(pg_conn, monkeypatch):
    value = date(2026, 3, 17)
    fecha_id = 20260317
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO dim_fecha (
                fecha_id, fecha, anio, mes, trimestre, semana_iso,
                dia_semana, es_fin_semana
            ) VALUES (%s, %s, 2026, 3, 1, 12, 2, FALSE)
            """,
            (fecha_id, value),
        )
        cur.execute(
            """
            INSERT INTO raw_files (filename, url, sha256, size_bytes, fecha_datos)
            VALUES ('published.zip', 'https://example.test/published.zip', 'published', 1, %s)
            RETURNING id
            """,
            (value,),
        )
        raw_id = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO ingestion_runs (
                raw_file_id, status, is_partial, rows_fact_published, finished_at
            ) VALUES (%s, 'completed', FALSE, 1, NOW())
            RETURNING id
            """,
            (raw_id,),
        )
        run_id = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO dim_provincia (codigo_iso, nombre, region)
            VALUES ('AR-C', 'CABA', 'AMBA') RETURNING provincia_sk
            """
        )
        provincia_sk = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO dim_comercio (id_comercio, id_bandera, comercio_bandera_nombre)
            VALUES (12, 1, 'Coto') RETURNING comercio_sk
            """
        )
        comercio_sk = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO dim_sucursal (
                id_comercio, id_bandera, id_sucursal, comercio_sk,
                provincia_sk, sucursales_localidad, sucursales_calle, sucursales_numero
            ) VALUES (12, 1, 7, %s, %s, 'Palermo', 'Av. Santa Fe', '3253')
            RETURNING sucursal_sk
            """,
            (comercio_sk, provincia_sk),
        )
        sucursal_sk = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO dim_producto (
                producto_identity, id_producto, productos_descripcion, productos_marca,
                contenido, contenido_unidad
            ) VALUES (
                'ean:7791234567895', '7791234567895', 'Leche entera 1 l', 'Marca', 1000, 'ml'
            )
            RETURNING producto_sk
            """
        )
        producto_sk = cur.fetchone()[0]
    _ensure_partition(pg_conn, fecha_id)
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO fact_precios (
                producto_sk, sucursal_sk, fecha_id, run_id,
                precio_lista, precio_promo1, leyenda_promo1,
                precio_promo2, leyenda_promo2, tiene_promo, fecha_observada_id
            ) VALUES (%s, %s, %s, %s, 100, 80, '20%% de descuento',
                      60, '3X2 LECHES', TRUE, %s)
            """,
            (producto_sk, sucursal_sk, fecha_id, run_id, fecha_id),
        )
        cur.execute(
            """
            INSERT INTO raw_files (filename, url, sha256, size_bytes, fecha_datos)
            VALUES ('failed.zip', 'https://example.test/failed.zip', 'failed', 1,
                    '2026-03-18')
            RETURNING id
            """
        )
        failed_raw_id = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO ingestion_runs (raw_file_id, status)
            VALUES (%s, 'failed')
            """,
            (failed_raw_id,),
        )
    pg_conn.commit()

    async def fetch(sql: str, *params):
        with pg_conn.cursor() as cur:
            cur.execute(re.sub(r"\$\d+", "%s", sql), params)
            columns = [column.name for column in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]

    async def fetchrow(sql: str, *params):
        rows = await fetch(sql, *params)
        return rows[0] if rows else None

    monkeypatch.setattr(buscador, "fetch", fetch)
    monkeypatch.setattr(ofertas, "fetch", fetch)
    monkeypatch.setattr(ofertas, "fetchrow", fetchrow)
    monkeypatch.setattr(locales, "fetch", fetch)
    monkeypatch.setattr(locales, "fetchrow", fetchrow)

    assert asyncio.run(buscador.get_product_dates(producto_sk)) == [
        {"fecha_id": fecha_id, "fecha": "2026-03-17"}
    ]
    store_page = asyncio.run(
        buscador.get_buscador(
            producto_sk=producto_sk, fecha_id=fecha_id,
            provincia=None, localidad=None, cadenas=None,
            location=None, limit=50, offset=0,
        )
    )
    assert (store_page["total"], store_page["hay_mas"]) == (1, False)
    store_offers = store_page["sucursales"]
    assert len(store_offers) == 1
    assert store_offers[0]["mejor_precio"] == Decimal("80.00")
    assert store_offers[0]["leyenda_promo"] == "20% de descuento"
    assert store_offers[0]["precio_condicional"] == Decimal("60.00")
    assert store_offers[0]["leyenda_condicional"] == "3X2 LECHES"
    assert store_offers[0]["condicion"] == "cantidad"
    # Source codes identify the store: id_sucursal is unique only in its chain.
    assert store_offers[0]["direccion"] == "Av. Santa Fe 3253"
    assert (
        store_offers[0]["id_comercio"], store_offers[0]["id_bandera"], store_offers[0]["id_sucursal"]
    ) == (12, 1, 7)
    # Per litre, from the store price: the conditional promotion is apart.
    assert store_offers[0]["precio_unitario"] == {"precio": Decimal("80.00"), "unidad": "l"}
    offers = asyncio.run(
        ofertas.get_ofertas(provincia=None, localidad=None, cadenas=None)
    )
    assert offers["fecha"] == "2026-03-17"
    assert len(offers["items"]) == 1
    assert offers["items"][0]["precio_promo"] == Decimal("60.00")
    assert offers["items"][0]["condicion"] == "cantidad"
    assert offers["items"][0]["fecha_precio"] == "2026-03-17"

    promos = asyncio.run(locales.get_store_promos(sucursal_sk))
    assert len(promos) == 1
    assert promos[0]["producto"] == "Leche entera 1 l"
    assert promos[0]["fecha_precio"] == "2026-03-17"
    assert promos[0]["condicion"] == "cantidad"
