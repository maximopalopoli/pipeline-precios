"""Integration coverage for upgrading existing retailer-local product data."""

from pathlib import Path

import pytest


_ROOT = Path(__file__).parent.parent.parent
_MIGRATIONS = sorted((_ROOT / "db" / "migrations").glob("*.sql"))
_SEEDS = _ROOT / "db" / "seeds" / "seed_provincias.sql"
_SCHEMA = "product_identity_upgrade_test"


@pytest.fixture()
def pre_identity_schema(pg_conn):
    """Create an isolated database schema at migration 009."""
    with pg_conn.cursor() as cur:
        cur.execute(f"CREATE SCHEMA {_SCHEMA}")
        cur.execute(f"SET search_path TO {_SCHEMA}")
        for migration in _MIGRATIONS:
            if migration.name >= "010_":
                break
            cur.execute(migration.read_text())
        cur.execute(_SEEDS.read_text())
    pg_conn.commit()

    try:
        yield pg_conn
    finally:
        pg_conn.rollback()
        with pg_conn.cursor() as cur:
            cur.execute("SET search_path TO public")
            cur.execute(f"DROP SCHEMA {_SCHEMA} CASCADE")
        pg_conn.commit()


def test_upgrade_separates_existing_internal_codes_by_retailer(pre_identity_schema):
    conn = pre_identity_schema
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO raw_files (filename, url, sha256, size_bytes, fecha_datos)
            VALUES ('upgrade.zip', 'http://example.test/upgrade.zip', 'upgrade', 1, '2026-03-15')
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
            """
            INSERT INTO dim_comercio (id_comercio, id_bandera)
            VALUES (1, 1), (2, 1)
            RETURNING comercio_sk, id_comercio
            """
        )
        comercios = {id_comercio: comercio_sk for comercio_sk, id_comercio in cur.fetchall()}
        cur.execute(
            """
            INSERT INTO dim_sucursal (id_comercio, id_bandera, id_sucursal, comercio_sk, provincia_sk)
            VALUES
                (1, 1, 10, %s, (SELECT provincia_sk FROM dim_provincia WHERE codigo_iso = 'AR-C')),
                (2, 1, 20, %s, (SELECT provincia_sk FROM dim_provincia WHERE codigo_iso = 'AR-C'))
            RETURNING sucursal_sk, id_comercio
            """,
            (comercios[1], comercios[2]),
        )
        sucursales = {id_comercio: sucursal_sk for sucursal_sk, id_comercio in cur.fetchall()}
        cur.execute(
            """
            INSERT INTO dim_producto (id_producto, es_ean, productos_descripcion)
            VALUES ('internal-milk-1l', FALSE, 'Leche interna')
            RETURNING producto_sk
            """
        )
        producto_sk = cur.fetchone()[0]
        cur.executemany(
            """
            INSERT INTO fact_precios (
                producto_sk, sucursal_sk, fecha_id, run_id, precio_lista, tiene_promo
            ) VALUES (%s, %s, 20260315, %s, %s, FALSE)
            """,
            [
                (producto_sk, sucursales[1], run_id, 100),
                (producto_sk, sucursales[2], run_id, 200),
            ],
        )
        cur.execute(
            """
            INSERT INTO pending_fact_precios (
                run_id, producto_sk, sucursal_sk, fecha_id, precio_lista, tiene_promo
            ) VALUES (%s, %s, %s, 20260315, 150, FALSE)
            """,
            (run_id, producto_sk, sucursales[2]),
        )
        cur.execute(
            """
            INSERT INTO agg_precios_diarios (
                producto_sk, fecha_id, provincia_sk, precio_lista_avg, precio_lista_min,
                precio_lista_max, precio_lista_median, precio_promo_avg, cantidad_obs,
                cantidad_con_promo
            ) VALUES (
                %s, 20260315,
                (SELECT provincia_sk FROM dim_provincia WHERE codigo_iso = 'AR-C'),
                150, 100, 200, 150, 150, 2, 0
            )
            """,
            (producto_sk,),
        )
    conn.commit()

    migration = next(m for m in _MIGRATIONS if m.name.startswith("010_"))
    with conn.cursor() as cur:
        cur.execute(migration.read_text())
    conn.commit()

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT p.producto_identity, f.precio_lista
            FROM fact_precios f
            JOIN dim_producto p ON p.producto_sk = f.producto_sk
            ORDER BY f.precio_lista
            """
        )
        facts = cur.fetchall()
        cur.execute(
            """
            SELECT p.producto_identity
            FROM pending_fact_precios f
            JOIN dim_producto p ON p.producto_sk = f.producto_sk
            """
        )
        (pending_identity,) = cur.fetchone()
        cur.execute(
            """
            SELECT p.producto_identity, a.precio_lista_avg, a.cantidad_obs
            FROM agg_precios_diarios a
            JOIN dim_producto p ON p.producto_sk = a.producto_sk
            ORDER BY p.producto_identity
            """
        )
        aggregates = cur.fetchall()

    assert facts == [
        ("chain:1:1:internal-milk-1l", 100),
        ("chain:2:1:internal-milk-1l", 200),
    ]
    assert pending_identity == "chain:2:1:internal-milk-1l"
    assert aggregates == [
        ("chain:1:1:internal-milk-1l", 100, 1),
        ("chain:2:1:internal-milk-1l", 200, 1),
    ]
