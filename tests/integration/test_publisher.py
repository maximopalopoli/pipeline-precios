"""Integration tests for atomic publication of a daily ingestion run."""

import asyncio
import re
from decimal import Decimal
from datetime import date
from pathlib import Path

import pytest

import etl.load.publisher as publisher
from etl.load.aggregator import aggregate
from etl.load.warehouse_loader import _ensure_partition
from etl.reconciliation import ProductReconciliation
from api.price_date import PRICE_DATE_SQL
from api.routes import buscador

_SEEDS = Path(__file__).parent.parent.parent / "db" / "seeds" / "seed_provincias.sql"
FECHA_ID = 20260315


@pytest.fixture(autouse=True)
def seed_data(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(_SEEDS.read_text())
        cur.execute(
            """
            INSERT INTO dim_fecha (fecha_id, fecha, anio, mes, trimestre, semana_iso, dia_semana, es_fin_semana)
            VALUES (%s, '2026-03-15', 2026, 3, 1, 11, 7, TRUE)
            """,
            (FECHA_ID,),
        )
        cur.execute(
            """
            INSERT INTO dim_comercio (id_comercio, id_bandera)
            VALUES (1, 1)
            RETURNING comercio_sk
            """
        )
        comercio_sk = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO dim_sucursal (id_comercio, id_bandera, id_sucursal, comercio_sk, provincia_sk)
            VALUES (1, 1, 1, %s, (SELECT provincia_sk FROM dim_provincia WHERE codigo_iso = 'AR-C'))
            RETURNING sucursal_sk
            """,
            (comercio_sk,),
        )
        sucursal_sk = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO dim_producto (producto_identity, id_producto, productos_descripcion)
            VALUES ('test:AAA', 'AAA', 'Producto A'), ('test:BBB', 'BBB', 'Producto B')
            RETURNING producto_sk, id_producto
            """
        )
        productos = {
            id_producto: producto_sk for producto_sk, id_producto in cur.fetchall()
        }
    pg_conn.commit()
    return {"sucursal_sk": sucursal_sk, "productos": productos}


def _create_run(
    pg_conn, filename: str, *, fecha_id: int = FECHA_ID, scope: str | None = None
) -> int:
    fecha = date(fecha_id // 10000, (fecha_id // 100) % 100, fecha_id % 100)
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO raw_files (filename, url, sha256, size_bytes, fecha_datos)
            VALUES (%s, 'http://example.test/data.zip', %s, 1, %s)
            RETURNING id
            """,
            (filename, filename, fecha),
        )
        raw_file_id = cur.fetchone()[0]
        cur.execute(
            """INSERT INTO ingestion_runs (raw_file_id, regional_scope)
               VALUES (%s, %s) RETURNING id""",
            (raw_file_id, scope),
        )
        return cur.fetchone()[0]


def _insert_fact(
    pg_conn, *, run_id: int, producto_sk: int, sucursal_sk: int, price: Decimal
) -> None:
    _ensure_partition(pg_conn, FECHA_ID)
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO fact_precios (
                producto_sk, sucursal_sk, fecha_id, run_id, precio_lista, tiene_promo
            ) VALUES (%s, %s, %s, %s, %s, FALSE)
            """,
            (producto_sk, sucursal_sk, FECHA_ID, run_id, price),
        )


def _insert_pending_fact(
    pg_conn, *, run_id: int, producto_sk: int, sucursal_sk: int, price: Decimal,
    fecha_id: int = FECHA_ID,
) -> None:
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO pending_fact_precios (
                run_id, producto_sk, sucursal_sk, fecha_id, precio_lista, tiene_promo
            ) VALUES (%s, %s, %s, %s, %s, FALSE)
            """,
            (run_id, producto_sk, sucursal_sk, fecha_id, price),
        )


def _seed_published_day(pg_conn, seed_data) -> int:
    run_id = _create_run(pg_conn, "published.zip")
    _insert_fact(
        pg_conn,
        run_id=run_id,
        producto_sk=seed_data["productos"]["AAA"],
        sucursal_sk=seed_data["sucursal_sk"],
        price=Decimal("100.00"),
    )
    _insert_fact(
        pg_conn,
        run_id=run_id,
        producto_sk=seed_data["productos"]["BBB"],
        sucursal_sk=seed_data["sucursal_sk"],
        price=Decimal("200.00"),
    )
    aggregate(pg_conn, FECHA_ID)
    pg_conn.commit()
    return run_id


def test_publish_replaces_the_full_daily_snapshot(pg_conn, seed_data):
    _seed_published_day(pg_conn, seed_data)
    pending_run_id = _create_run(pg_conn, "rectified.zip")
    _insert_pending_fact(
        pg_conn,
        run_id=pending_run_id,
        producto_sk=seed_data["productos"]["AAA"],
        sucursal_sk=seed_data["sucursal_sk"],
        price=Decimal("125.00"),
    )
    pg_conn.commit()

    result = publisher.publish_pending_facts(
        pg_conn,
        run_id=pending_run_id,
        fecha_id=FECHA_ID,
        reconciliation=ProductReconciliation(1, 0, 0, 0, 1, 1),
    )
    pg_conn.commit()

    assert result.rows_fact == 1
    assert result.rows_aggregated == 1
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*), MAX(precio_lista) FROM fact_precios WHERE fecha_id = %s",
            (FECHA_ID,),
        )
        rows, price = cur.fetchone()
        assert rows == 1
        assert price == Decimal("125.00")
        cur.execute(
            "SELECT COUNT(*) FROM agg_precios_diarios WHERE fecha_id = %s",
            (FECHA_ID,),
        )
        assert cur.fetchone()[0] == 1
        cur.execute(
            "SELECT status, rows_valid FROM ingestion_runs WHERE id = %s",
            (pending_run_id,),
        )
        assert cur.fetchone() == ("completed", 1)
        cur.execute(
            "SELECT COUNT(*) FROM pending_fact_precios WHERE run_id = %s",
            (pending_run_id,),
        )
        assert cur.fetchone()[0] == 0
        cur.execute(
            """
            SELECT COUNT(*)
            FROM pg_index index_definition
            JOIN pg_class leaf ON leaf.oid = index_definition.indrelid
            WHERE leaf.relname = 'fact_precios_20260315'
            """
        )
        assert cur.fetchone()[0] >= 4
        cur.execute(
            """
            SELECT delete_existing_seconds, insert_facts_seconds,
                   aggregate_delete_seconds, aggregate_insert_seconds, cleanup_seconds
                   , partition_swap_seconds
            FROM ingestion_publication_metrics WHERE run_id = %s
            """,
            (pending_run_id,),
        )
        assert all(value >= 0 for value in cur.fetchone())


def test_publish_rectifies_a_day_with_a_legacy_candidate_leaf(pg_conn, seed_data):
    first_run_id = _create_run(pg_conn, "initial-publish.zip")
    _insert_pending_fact(
        pg_conn,
        run_id=first_run_id,
        producto_sk=seed_data["productos"]["AAA"],
        sucursal_sk=seed_data["sucursal_sk"],
        price=Decimal("100.00"),
    )
    pg_conn.commit()
    publisher.publish_pending_facts(
        pg_conn,
        run_id=first_run_id,
        fecha_id=FECHA_ID,
        reconciliation=ProductReconciliation(1, 0, 0, 0, 1, 1),
    )
    pg_conn.commit()

    with pg_conn.cursor() as cur:
        cur.execute(
            "ALTER TABLE fact_precios_20260315 "
            "RENAME TO fact_precios_20260315_run_legacy"
        )
    pg_conn.commit()

    second_run_id = _create_run(pg_conn, "second-publish.zip")
    _insert_pending_fact(
        pg_conn,
        run_id=second_run_id,
        producto_sk=seed_data["productos"]["AAA"],
        sucursal_sk=seed_data["sucursal_sk"],
        price=Decimal("125.00"),
    )
    pg_conn.commit()
    publisher.publish_pending_facts(
        pg_conn,
        run_id=second_run_id,
        fecha_id=FECHA_ID,
        reconciliation=ProductReconciliation(1, 0, 0, 0, 1, 1),
    )
    pg_conn.commit()

    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT precio_lista FROM fact_precios WHERE fecha_id = %s", (FECHA_ID,)
        )
        assert cur.fetchone()[0] == Decimal("125.00")
        cur.execute(
            """
            SELECT child.relname
            FROM pg_inherits AS inheritance
            JOIN pg_class AS parent ON parent.oid = inheritance.inhparent
            JOIN pg_class AS child ON child.oid = inheritance.inhrelid
            WHERE parent.relname = 'fact_precios_202603'
              AND child.relname = 'fact_precios_20260315'
            """
        )
        assert cur.fetchone() == ("fact_precios_20260315",)


def test_successful_retry_clears_a_previous_failure_message(pg_conn, seed_data):
    run_id = _create_run(pg_conn, "retried-run.zip")
    _insert_pending_fact(
        pg_conn,
        run_id=run_id,
        producto_sk=seed_data["productos"]["AAA"],
        sucursal_sk=seed_data["sucursal_sk"],
        price=Decimal("125.00"),
    )
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            UPDATE ingestion_runs
               SET status = 'failed', finished_at = NOW(), error_message = 'temporary failure'
             WHERE id = %s
            """,
            (run_id,),
        )
    pg_conn.commit()

    publisher.publish_pending_facts(
        pg_conn,
        run_id=run_id,
        fecha_id=FECHA_ID,
        reconciliation=ProductReconciliation(1, 0, 0, 0, 1, 1),
    )
    pg_conn.commit()

    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT status, error_message FROM ingestion_runs WHERE id = %s",
            (run_id,),
        )
        assert cur.fetchone() == ("completed", None)


def test_publication_finished_at_uses_wall_clock_not_transaction_start(
    pg_conn, seed_data
):
    run_id = _create_run(pg_conn, "finished-at.zip")
    _insert_pending_fact(
        pg_conn,
        run_id=run_id,
        producto_sk=seed_data["productos"]["AAA"],
        sucursal_sk=seed_data["sucursal_sk"],
        price=Decimal("125.00"),
    )
    pg_conn.commit()

    with pg_conn.cursor() as cur:
        cur.execute("SELECT transaction_timestamp()")
        cur.execute("SELECT pg_sleep(0.02)")

    publisher.publish_pending_facts(
        pg_conn,
        run_id=run_id,
        fecha_id=FECHA_ID,
        reconciliation=ProductReconciliation(1, 0, 0, 0, 1, 1),
    )

    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT finished_at > transaction_timestamp() "
            "FROM ingestion_runs WHERE id = %s",
            (run_id,),
        )
        assert cur.fetchone()[0] is True


def test_partial_completion_keeps_the_visible_snapshot_unchanged(pg_conn, seed_data):
    _seed_published_day(pg_conn, seed_data)
    partial_run_id = _create_run(pg_conn, "partial.zip")
    with pg_conn.cursor() as cur:
        cur.execute(
            "UPDATE ingestion_runs SET is_partial = TRUE WHERE id = %s",
            (partial_run_id,),
        )
    _insert_pending_fact(
        pg_conn,
        run_id=partial_run_id,
        producto_sk=seed_data["productos"]["AAA"],
        sucursal_sk=seed_data["sucursal_sk"],
        price=Decimal("125.00"),
    )
    pg_conn.commit()

    result = publisher.complete_partial_run(
        pg_conn,
        run_id=partial_run_id,
        reconciliation=ProductReconciliation(1, 0, 0, 0, 1, 0, is_partial=True),
    )
    pg_conn.commit()

    assert result.rows_fact == 0
    assert result.rows_aggregated == 0
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*), MAX(precio_lista) FROM fact_precios WHERE fecha_id = %s",
            (FECHA_ID,),
        )
        assert cur.fetchone() == (2, Decimal("200.00"))
        cur.execute(
            """
            SELECT rows_fact_pending, rows_fact_published, is_partial
            FROM ingestion_runs WHERE id = %s
            """,
            (partial_run_id,),
        )
        assert cur.fetchone() == (1, 0, True)
        cur.execute(
            "SELECT COUNT(*) FROM pending_fact_precios WHERE run_id = %s",
            (partial_run_id,),
        )
        assert cur.fetchone()[0] == 1


def test_failed_publication_keeps_the_previous_snapshot(
    pg_conn, seed_data, monkeypatch
):
    _seed_published_day(pg_conn, seed_data)
    pending_run_id = _create_run(pg_conn, "failed-rectification.zip")
    _insert_pending_fact(
        pg_conn,
        run_id=pending_run_id,
        producto_sk=seed_data["productos"]["AAA"],
        sucursal_sk=seed_data["sucursal_sk"],
        price=Decimal("125.00"),
    )
    pg_conn.commit()

    def fail_aggregation(conn, fecha_id):
        raise RuntimeError("aggregation failed")

    monkeypatch.setattr(publisher, "aggregate", fail_aggregation)

    with pytest.raises(RuntimeError, match="aggregation failed"):
        publisher.publish_pending_facts(
            pg_conn,
            run_id=pending_run_id,
            fecha_id=FECHA_ID,
            reconciliation=ProductReconciliation(1, 0, 0, 0, 1, 1),
        )
    pg_conn.rollback()

    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*), MIN(precio_lista), MAX(precio_lista) FROM fact_precios WHERE fecha_id = %s",
            (FECHA_ID,),
        )
        assert cur.fetchone() == (2, Decimal("100.00"), Decimal("200.00"))
        cur.execute(
            "SELECT COUNT(*) FROM agg_precios_diarios WHERE fecha_id = %s",
            (FECHA_ID,),
        )
        assert cur.fetchone()[0] == 2
        cur.execute(
            "SELECT status FROM ingestion_runs WHERE id = %s", (pending_run_id,)
        )
        assert cur.fetchone()[0] == "running"
        cur.execute(
            "SELECT COUNT(*) FROM pending_fact_precios WHERE run_id = %s",
            (pending_run_id,),
        )
        assert cur.fetchone()[0] == 1


def _add_day(pg_conn, fecha_id: int) -> None:
    value = date(fecha_id // 10000, (fecha_id // 100) % 100, fecha_id % 100)
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO dim_fecha (
                fecha_id, fecha, anio, mes, trimestre, semana_iso, dia_semana, es_fin_semana
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                fecha_id, value, value.year, value.month,
                (value.month - 1) // 3 + 1, value.isocalendar().week,
                value.isoweekday(), value.isoweekday() >= 6,
            ),
        )


def _publish_day(
    pg_conn, *, fecha_id: int, scope: str | None, stores: list[tuple],
    label: str = "published",
) -> tuple:
    run_id = _create_run(
        pg_conn, f"{label}-{fecha_id}-{scope}.zip", fecha_id=fecha_id, scope=scope
    )
    for sucursal_sk, producto_sk, price in stores:
        _insert_pending_fact(
            pg_conn, run_id=run_id, sucursal_sk=sucursal_sk,
            producto_sk=producto_sk, price=Decimal(price), fecha_id=fecha_id,
        )
    pg_conn.commit()
    result = publisher.publish_pending_facts(
        pg_conn,
        run_id=run_id,
        fecha_id=fecha_id,
        reconciliation=ProductReconciliation(len(stores), 0, 0, 0, len(stores), len(stores)),
    )
    pg_conn.commit()
    return run_id, result


def test_missing_store_prices_are_carried_until_the_store_returns(
    pg_conn, seed_data, monkeypatch
):
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO dim_comercio (id_comercio, id_bandera) VALUES (2, 1) RETURNING comercio_sk"
        )
        comercio_sk = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO dim_sucursal (
                id_comercio, id_bandera, id_sucursal, comercio_sk, provincia_sk
            ) VALUES (
                2, 1, 1, %s,
                (SELECT provincia_sk FROM dim_provincia WHERE codigo_iso = 'AR-C')
            ) RETURNING sucursal_sk
            """,
            (comercio_sk,),
        )
        missing_store = cur.fetchone()[0]
    first_store = seed_data["sucursal_sk"]
    product_a = seed_data["productos"]["AAA"]
    product_b = seed_data["productos"]["BBB"]
    initial = [(first_store, product_a, "100"), (missing_store, product_b, "200")]
    _publish_day(pg_conn, fecha_id=FECHA_ID, scope="amba", stores=initial)
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            UPDATE fact_precios
            SET precio_promo1 = 150, leyenda_promo1 = 'Old promotion', tiene_promo = TRUE
            WHERE fecha_id = %s AND sucursal_sk = %s
            """,
            (FECHA_ID, missing_store),
        )
    pg_conn.commit()

    for fecha_id in (20260316, 20260317):
        _add_day(pg_conn, fecha_id)
        run_id, result = _publish_day(
            pg_conn, fecha_id=fecha_id, scope="amba",
            stores=[(first_store, product_a, "110")],
        )
        assert result.rows_fact == 2
        assert result.rows_carried_forward == 1
        with pg_conn.cursor() as cur:
            cur.execute(
                """
                SELECT sucursal_sk, precio_lista, fecha_observada_id,
                       precio_promo1, tiene_promo, {price_date} AS fecha_precio
                FROM fact_precios f WHERE fecha_id = %s ORDER BY sucursal_sk
                """.format(price_date=PRICE_DATE_SQL),
                (fecha_id,),
            )
            assert cur.fetchall() == [
                (first_store, Decimal("110.00"), fecha_id, None, False,
                 f"{fecha_id // 10000:04d}-{(fecha_id // 100) % 100:02d}-{fecha_id % 100:02d}"),
                (missing_store, Decimal("200.00"), FECHA_ID, None, False,
                 "2026-03-15"),
            ]
            cur.execute(
                """
                SELECT rows_fact_pending, rows_fact_published,
                       rows_fact_carried_forward, rows_fact_snapshot
                FROM ingestion_runs WHERE id = %s
                """,
                (run_id,),
            )
            assert cur.fetchone() == (1, 1, 1, 2)

        if fecha_id == 20260316:
            async def fetch_prices(sql, *params):
                with pg_conn.cursor() as cur:
                    cur.execute(re.sub(r"\$\d+", "%s", sql), params)
                    columns = [column.name for column in cur.description]
                    return [dict(zip(columns, row)) for row in cur.fetchall()]

            monkeypatch.setattr(buscador, "fetch", fetch_prices)
            offers = asyncio.run(
                buscador.get_buscador(
                    producto_sk=product_b, fecha_id=fecha_id,
                    provincia=None, localidad=None, cadenas=None,
                    lat=None, lon=None, radius=5,
                )
            )
            assert len(offers) == 1
            assert offers[0]["fecha_precio"] == "2026-03-15"

    _add_day(pg_conn, 20260318)
    _, result = _publish_day(
        pg_conn, fecha_id=20260318, scope="amba",
        stores=[(first_store, product_a, "120"), (missing_store, product_b, "230")],
    )
    assert result.rows_carried_forward == 0
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT precio_lista, fecha_observada_id
            FROM fact_precios
            WHERE fecha_id = 20260318 AND sucursal_sk = %s
            """,
            (missing_store,),
        )
        assert cur.fetchone() == (Decimal("230.00"), 20260318)


def test_first_scoped_day_does_not_carry_national_stores(pg_conn, seed_data):
    store = seed_data["sucursal_sk"]
    product_a = seed_data["productos"]["AAA"]
    product_b = seed_data["productos"]["BBB"]
    _publish_day(
        pg_conn, fecha_id=FECHA_ID, scope=None,
        stores=[(store, product_a, "100"), (store, product_b, "200")],
    )
    _add_day(pg_conn, 20260316)
    _, result = _publish_day(
        pg_conn, fecha_id=20260316, scope="amba",
        stores=[(store, product_a, "110")],
    )
    assert result.rows_fact == 1
    assert result.rows_carried_forward == 0


def test_same_day_rectification_keeps_an_unreported_store(pg_conn, seed_data):
    store = seed_data["sucursal_sk"]
    product_a = seed_data["productos"]["AAA"]
    product_b = seed_data["productos"]["BBB"]
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO dim_sucursal (
                id_comercio, id_bandera, id_sucursal, comercio_sk, provincia_sk
            ) SELECT 1, 1, 2, comercio_sk, provincia_sk
              FROM dim_sucursal WHERE sucursal_sk = %s
            RETURNING sucursal_sk
            """,
            (store,),
        )
        other_store = cur.fetchone()[0]

    _publish_day(
        pg_conn, fecha_id=FECHA_ID, scope="amba",
        stores=[(store, product_a, "100"), (other_store, product_b, "200")],
    )
    _, result = _publish_day(
        pg_conn, fecha_id=FECHA_ID, scope="amba", label="rectified",
        stores=[(store, product_a, "110")],
    )
    assert result.rows_carried_forward == 1
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT sucursal_sk, precio_lista, fecha_observada_id
            FROM fact_precios WHERE fecha_id = %s ORDER BY sucursal_sk
            """,
            (FECHA_ID,),
        )
        assert cur.fetchall() == [
            (store, Decimal("110.00"), FECHA_ID),
            (other_store, Decimal("200.00"), FECHA_ID),
        ]
