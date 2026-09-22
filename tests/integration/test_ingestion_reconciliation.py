from etl.reconciliation import (
    ProductReconciliation,
    record_product_reconciliation,
    record_product_rejections,
)


def test_record_product_reconciliation_persists_all_counts(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO raw_files (filename, url, sha256, size_bytes, fecha_datos)
            VALUES ('reconciliation.zip', 'https://example.test/reconciliation.zip', 'reconciliation', 1, '2026-03-15')
            RETURNING id
            """
        )
        raw_file_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO ingestion_runs (raw_file_id) VALUES (%s) RETURNING id",
            (raw_file_id,),
        )
        run_id = cur.fetchone()[0]

    counts = ProductReconciliation(
        rows_valid=10,
        rows_rejected=2,
        rows_without_keys=1,
        rows_deduplicated=2,
        rows_fact_pending=7,
        rows_fact_published=7,
    )
    record_product_reconciliation(pg_conn, run_id, counts)
    pg_conn.commit()

    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT rows_productos_valid, rows_productos_rejected,
                   rows_productos_without_keys, rows_productos_deduplicated,
                   rows_fact_pending,
                   rows_fact_published
            FROM ingestion_runs
            WHERE id = %s
            """,
            (run_id,),
        )
        persisted = cur.fetchone()

    assert persisted == (10, 2, 1, 2, 7, 7)


def test_record_product_rejections_keeps_reason_and_source(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO raw_files (filename, url, sha256, size_bytes, fecha_datos)
            VALUES ('rejected.zip', 'https://example.test/rejected.zip', 'rejected', 1, '2026-03-15')
            RETURNING id
            """
        )
        raw_file_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO ingestion_runs (raw_file_id) VALUES (%s) RETURNING id",
            (raw_file_id,),
        )
        run_id = cur.fetchone()[0]

    recorded = record_product_rejections(
        pg_conn,
        run_id=run_id,
        source="fact_key_resolution",
        rejected=[
            {
                "row": {"id_producto": "missing", "precio": "12.50"},
                "reason": "producto_sk not found",
            }
        ],
    )
    pg_conn.commit()

    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT source_file, raw_data, rejection_reason FROM rejected_rows WHERE run_id = %s",
            (run_id,),
        )
        persisted = cur.fetchone()

    assert recorded == 1
    assert persisted == (
        "fact_key_resolution",
        '{"id_producto": "missing", "precio": "12.50"}',
        "producto_sk not found",
    )
