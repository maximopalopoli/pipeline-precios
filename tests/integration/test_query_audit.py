from etl.query_audit import audit_queries, representative_queries
from etl.load.warehouse_loader import _ensure_partition


def test_audit_queries_executes_representative_plans_against_published_facts(pg_conn):
    _ensure_partition(pg_conn, 20260317)
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO raw_files (filename, url, sha256, size_bytes, fecha_datos)
            VALUES ('audit.zip', 'https://example.test/audit.zip', 'audit', 1, '2026-03-17')
            RETURNING id
            """
        )
        raw_file_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO ingestion_runs (raw_file_id, status) VALUES (%s, 'completed') RETURNING id",
            (raw_file_id,),
        )
        run_id = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO dim_provincia (codigo_iso, nombre, region)
            VALUES ('AR-B', 'Buenos Aires', 'Pampeana') RETURNING provincia_sk
            """
        )
        provincia_sk = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO dim_comercio (id_comercio, id_bandera, comercio_bandera_nombre)
            VALUES (1, 1, 'Cadena') RETURNING comercio_sk
            """
        )
        comercio_sk = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO dim_sucursal (
                id_comercio, id_bandera, id_sucursal, comercio_sk, provincia_sk,
                sucursales_localidad
            ) VALUES (1, 1, 1, %s, %s, 'La Plata') RETURNING sucursal_sk
            """,
            (comercio_sk, provincia_sk),
        )
        sucursal_sk = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO dim_producto (producto_identity, id_producto, productos_descripcion)
            VALUES ('ean:7790895000107', '7790895000107', 'Producto')
            RETURNING producto_sk
            """
        )
        producto_sk = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO dim_fecha (
                fecha_id, fecha, anio, mes, trimestre, semana_iso, dia_semana, es_fin_semana
            ) VALUES (20260317, '2026-03-17', 2026, 3, 1, 12, 2, FALSE)
            """
        )
        cur.execute(
            """
            INSERT INTO fact_precios (
                producto_sk, sucursal_sk, fecha_id, run_id, precio_lista,
                precio_promo1, tiene_promo
            ) VALUES (%s, %s, 20260317, %s, 100, 80, TRUE)
            """,
            (producto_sk, sucursal_sk, run_id),
        )
    pg_conn.commit()

    summaries = audit_queries(pg_conn, representative_queries(pg_conn))

    assert [summary.name for summary in summaries] == [
        "product_offer_lookup",
        "daily_promotion_scan",
    ]
    assert all(summary.execution_time_ms >= 0 for summary in summaries)
