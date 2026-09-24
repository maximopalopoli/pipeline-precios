"""Regression coverage for the promotion-flag data backfill."""

from pathlib import Path

from etl.load.warehouse_loader import _ensure_partition


_ROOT = Path(__file__).parent.parent.parent
_SEEDS = _ROOT / "db" / "seeds" / "seed_provincias.sql"
_MIGRATION = _ROOT / "db" / "migrations" / "018_normalize_effective_promotion_flags.sql"


def test_promotion_flag_migration_corrects_historical_facts_and_aggregates(pg_conn):
    _ensure_partition(pg_conn, 20260315)
    with pg_conn.cursor() as cur:
        cur.execute(_SEEDS.read_text())
        cur.execute(
            """
            INSERT INTO raw_files (filename, url, sha256, size_bytes, fecha_datos)
            VALUES ('historical.zip', 'https://example.test/historical.zip', 'historical', 1, '2026-03-15')
            RETURNING id
            """
        )
        raw_file_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO ingestion_runs (raw_file_id) VALUES (%s) RETURNING id",
            (raw_file_id,),
        )
        run_id = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO dim_fecha (fecha_id, fecha, anio, mes, trimestre, semana_iso, dia_semana, es_fin_semana)
            VALUES (20260315, '2026-03-15', 2026, 3, 1, 11, 7, TRUE)
            """
        )
        cur.execute(
            "INSERT INTO dim_comercio (id_comercio, id_bandera) VALUES (1, 1) RETURNING comercio_sk"
        )
        comercio_sk = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO dim_sucursal (id_comercio, id_bandera, id_sucursal, comercio_sk, provincia_sk)
            VALUES
                (1, 1, 10, %s, (SELECT provincia_sk FROM dim_provincia WHERE codigo_iso = 'AR-C')),
                (1, 1, 20, %s, (SELECT provincia_sk FROM dim_provincia WHERE codigo_iso = 'AR-C'))
            RETURNING sucursal_sk
            """,
            (comercio_sk, comercio_sk),
        )
        sucursal_one, sucursal_two = [row[0] for row in cur.fetchall()]
        cur.execute(
            """
            INSERT INTO dim_producto (producto_identity, id_producto, es_ean, productos_descripcion)
            VALUES ('ean:7790001234567', '7790001234567', TRUE, 'Producto histórico')
            RETURNING producto_sk
            """
        )
        producto_sk = cur.fetchone()[0]
        cur.executemany(
            """
            INSERT INTO fact_precios (
                producto_sk, sucursal_sk, fecha_id, run_id, precio_lista, precio_promo1, precio_promo2, tiene_promo
            ) VALUES (%s, %s, 20260315, %s, 100, %s, %s, TRUE)
            """,
            [
                (producto_sk, sucursal_one, run_id, 0, None),
                (producto_sk, sucursal_two, run_id, None, 200),
            ],
        )
        cur.execute(
            """
            INSERT INTO agg_precios_diarios (
                producto_sk, fecha_id, provincia_sk, cantidad_obs, cantidad_con_promo
            ) VALUES (
                %s, 20260315,
                (SELECT provincia_sk FROM dim_provincia WHERE codigo_iso = 'AR-C'),
                2, 2
            )
            """,
            (producto_sk,),
        )
    pg_conn.commit()

    with pg_conn.cursor() as cur:
        cur.execute(_MIGRATION.read_text())
    pg_conn.commit()

    with pg_conn.cursor() as cur:
        cur.execute("SELECT tiene_promo FROM fact_precios ORDER BY sucursal_sk")
        flags = [row[0] for row in cur.fetchall()]
        cur.execute(
            "SELECT cantidad_con_promo FROM agg_precios_diarios WHERE producto_sk = %s",
            (producto_sk,),
        )
        promo_count = cur.fetchone()[0]

    assert flags == [False, False]
    assert promo_count == 0
