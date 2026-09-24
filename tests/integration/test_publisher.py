"""Integration tests for atomic publication of a daily ingestion run."""

from decimal import Decimal
from pathlib import Path

import pytest

import etl.load.publisher as publisher
from etl.load.aggregator import aggregate
from etl.load.warehouse_loader import _ensure_partition
from etl.reconciliation import ProductReconciliation

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


def _create_run(pg_conn, filename: str) -> int:
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO raw_files (filename, url, sha256, size_bytes, fecha_datos)
            VALUES (%s, 'http://example.test/data.zip', %s, 1, '2026-03-15')
            RETURNING id
            """,
            (filename, filename),
        )
        raw_file_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO ingestion_runs (raw_file_id) VALUES (%s) RETURNING id",
            (raw_file_id,),
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
    pg_conn, *, run_id: int, producto_sk: int, sucursal_sk: int, price: Decimal
) -> None:
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO pending_fact_precios (
                run_id, producto_sk, sucursal_sk, fecha_id, precio_lista, tiene_promo
            ) VALUES (%s, %s, %s, %s, %s, FALSE)
            """,
            (run_id, producto_sk, sucursal_sk, FECHA_ID, price),
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
            WHERE leaf.relname LIKE 'fact_precios_20260315_run_%'
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
