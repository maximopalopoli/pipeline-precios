"""Integration coverage for the shared staged-product streaming path."""

from datetime import date
from pathlib import Path

import psycopg

from etl.ingestion_processing import load_staged_dimensions, stream_pending_product_facts
from etl.staging import loader as staging_loader

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


def test_streaming_product_processing_loads_pending_facts_in_chunks(pg_conn, sample_zip_path):
    with pg_conn.cursor() as cur:
        cur.execute((_SEEDS / "seed_provincias.sql").read_text())
        cur.execute((_SEEDS / "seed_fechas.sql").read_text())
    run_id = _create_run(pg_conn)
    staging_loader.load(pg_conn, sample_zip_path, run_id)
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO stg_productos
            SELECT * FROM stg_productos
            WHERE run_id = %s
            LIMIT 1
            """,
            (run_id,),
        )

    dimensions = load_staged_dimensions(pg_conn, run_id)
    pg_conn.commit()

    heartbeats: list[None] = []
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
        heartbeat=lambda: heartbeats.append(None),
    )
    pg_conn.commit()

    assert dimensions.rows_comercios == 2
    assert dimensions.rows_sucursales == 3
    assert dimensions.rows_productos == 5
    assert dimensions.rows_rejected == 0
    assert result.chunks == 4
    assert result.rows_valid == 8
    assert result.rows_fact == 7
    assert result.rows_rejected == 0
    assert result.rows_without_keys == 0
    assert result.rows_deduplicated == 1
    assert len(heartbeats) == result.chunks
    with pg_conn.cursor() as cur:
        cur.execute("SELECT rows_loaded FROM ingestion_runs WHERE id = %s", (run_id,))
        assert cur.fetchone()[0] == 7
        cur.execute("SELECT COUNT(*) FROM pending_fact_precios WHERE run_id = %s", (run_id,))
        assert cur.fetchone()[0] == 7
