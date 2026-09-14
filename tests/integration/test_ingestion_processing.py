"""Integration coverage for the shared staged-product streaming path."""

from datetime import date
from pathlib import Path

import pandas as pd
import psycopg

from etl.ingestion_processing import stream_pending_product_facts
from etl.load import warehouse_loader
from etl.staging import loader as staging_loader
from etl.transform.cleaner import clean_comercios, clean_sucursales

_SEEDS = Path(__file__).parent.parent.parent / "db" / "seeds"
_RUN_DATE = date(2026, 3, 15)


def _create_run(conn) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO raw_files (filename, url, sha256, size_bytes, fecha_datos)
            VALUES ('stream.zip', 'http://example.test/data.zip', 'stream-test', 1, %s)
            RETURNING id
            """,
            (_RUN_DATE,),
        )
        raw_file_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO ingestion_runs (raw_file_id) VALUES (%s) RETURNING id",
            (raw_file_id,),
        )
        return cur.fetchone()[0]


def _read_staging(conn, table: str, run_id: int) -> pd.DataFrame:
    with conn.cursor() as cur:
        cur.execute(f"SELECT * FROM {table} WHERE run_id = %s", (run_id,))
        return pd.DataFrame(cur.fetchall(), columns=[column[0] for column in cur.description])


def test_streaming_product_processing_loads_pending_facts_in_chunks(pg_conn, sample_zip_path):
    with pg_conn.cursor() as cur:
        cur.execute((_SEEDS / "seed_provincias.sql").read_text())
        cur.execute((_SEEDS / "seed_fechas.sql").read_text())
    run_id = _create_run(pg_conn)
    staging_loader.load(pg_conn, sample_zip_path, run_id)

    comercios = clean_comercios(_read_staging(pg_conn, "stg_comercios", run_id)).clean
    sucursales = clean_sucursales(_read_staging(pg_conn, "stg_sucursales", run_id)).clean
    warehouse_loader.load_dimensions_no_producto(pg_conn, comercios, sucursales)
    warehouse_loader.load_dim_produto_from_staging(pg_conn, run_id)
    pg_conn.commit()

    result = stream_pending_product_facts(
        pg_conn,
        lambda: psycopg.connect(
            host=pg_conn.info.host,
            port=pg_conn.info.port,
            user=pg_conn.info.user,
            dbname=pg_conn.info.dbname,
        ),
        run_id=run_id,
        run_date=_RUN_DATE,
        chunk_size=2,
    )
    pg_conn.commit()

    assert result.chunks == 4
    assert result.rows_valid == 7
    assert result.rows_fact == 7
    with pg_conn.cursor() as cur:
        cur.execute("SELECT rows_loaded FROM ingestion_runs WHERE id = %s", (run_id,))
        assert cur.fetchone()[0] == 7
        cur.execute("SELECT COUNT(*) FROM pending_fact_precios WHERE run_id = %s", (run_id,))
        assert cur.fetchone()[0] == 7
