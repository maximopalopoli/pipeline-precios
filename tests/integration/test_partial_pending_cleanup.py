"""Pending facts of superseded partial runs are deleted when a run opens."""

from datetime import date

import pytest

from etl.development_ingestion import DevelopmentIngestionPolicy
from etl.ingestion_run import open_ingestion_run

FECHA_ID = 20260315


@pytest.fixture
def seeded(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO dim_fecha (
                fecha_id, fecha, anio, mes, trimestre, semana_iso, dia_semana, es_fin_semana
            ) VALUES (%s, '2026-03-15', 2026, 3, 1, 11, 7, TRUE)
            """,
            (FECHA_ID,),
        )
        cur.execute(
            "INSERT INTO dim_comercio (id_comercio, id_bandera) VALUES (1, 1) RETURNING comercio_sk"
        )
        comercio_sk = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO dim_sucursal (id_comercio, id_bandera, id_sucursal, comercio_sk)
            VALUES (1, 1, 1, %s) RETURNING sucursal_sk
            """,
            (comercio_sk,),
        )
        sucursal_sk = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO dim_producto (producto_identity, id_producto, productos_descripcion)
            VALUES ('test:AAA', 'AAA', 'Producto A') RETURNING producto_sk
            """
        )
        producto_sk = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO raw_files (filename, url, sha256, size_bytes, fecha_datos)
            VALUES ('data.zip', 'http://example.test/data.zip', 'data', 1, '2026-03-15')
            RETURNING id
            """
        )
        raw_file_id = cur.fetchone()[0]
    pg_conn.commit()
    return {
        "raw_file_id": raw_file_id,
        "sucursal_sk": sucursal_sk,
        "producto_sk": producto_sk,
    }


def _run_with_pending(pg_conn, seeded, *, is_partial: bool, status: str) -> int:
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO ingestion_runs (raw_file_id, status, is_partial)
            VALUES (%s, %s, %s) RETURNING id
            """,
            (seeded["raw_file_id"], status, is_partial),
        )
        run_id = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO pending_fact_precios (
                run_id, producto_sk, sucursal_sk, fecha_id, precio_lista
            ) VALUES (%s, %s, %s, %s, 100)
            """,
            (run_id, seeded["producto_sk"], seeded["sucursal_sk"], FECHA_ID),
        )
    pg_conn.commit()
    return run_id


def _pending_run_ids(pg_conn) -> set[int]:
    with pg_conn.cursor() as cur:
        cur.execute("SELECT DISTINCT run_id FROM pending_fact_precios")
        return {row[0] for row in cur.fetchall()}


def test_opening_a_run_keeps_only_the_newest_completed_partial_pending(
    pg_conn, seeded
):
    old_partial = _run_with_pending(pg_conn, seeded, is_partial=True, status="completed")
    failed_partial = _run_with_pending(pg_conn, seeded, is_partial=True, status="failed")
    failed_full = _run_with_pending(pg_conn, seeded, is_partial=False, status="failed")
    running_full = _run_with_pending(pg_conn, seeded, is_partial=False, status="running")
    newest_partial = _run_with_pending(pg_conn, seeded, is_partial=True, status="completed")

    open_ingestion_run(pg_conn, seeded["raw_file_id"], fecha_logica=date(2026, 3, 15))

    assert old_partial not in _pending_run_ids(pg_conn)
    assert _pending_run_ids(pg_conn) == {
        failed_partial,
        failed_full,
        running_full,
        newest_partial,
    }


def test_a_new_partial_run_keeps_the_previous_partial_until_it_completes(
    pg_conn, seeded
):
    previous_partial = _run_with_pending(
        pg_conn, seeded, is_partial=True, status="completed"
    )

    new_run_id = open_ingestion_run(
        pg_conn,
        seeded["raw_file_id"],
        fecha_logica=date(2026, 3, 15),
        development_policy=DevelopmentIngestionPolicy(max_product_rows=10),
    )

    assert _pending_run_ids(pg_conn) == {previous_partial}
    with pg_conn.cursor() as cur:
        cur.execute(
            "UPDATE ingestion_runs SET status = 'completed' WHERE id = %s",
            (new_run_id,),
        )
    pg_conn.commit()

    open_ingestion_run(pg_conn, seeded["raw_file_id"], fecha_logica=date(2026, 3, 15))

    assert _pending_run_ids(pg_conn) == set()
