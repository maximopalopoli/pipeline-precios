import pytest

from etl.reconciliation import (
    CLEANING_SOURCE,
    KEY_RESOLUTION_SOURCE,
    ProductReconciliation,
    record_product_reconciliation,
    record_product_rejections,
)
from etl.rejection_counts import add_rejection_counts


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
    add_rejection_counts(
        pg_conn, run_id=run_id, source_file=CLEANING_SOURCE, counts={"a": 1, "b": 1}
    )
    add_rejection_counts(
        pg_conn, run_id=run_id, source_file=KEY_RESOLUTION_SOURCE, counts={"c": 1}
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
                "code": "producto_not_found",
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


def test_record_product_reconciliation_rejects_an_invalid_raw_count(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO raw_files (filename, url, sha256, size_bytes, fecha_datos)
            VALUES ('invalid-count.zip', 'https://example.test/invalid-count.zip', 'invalid-count', 1, '2026-03-15')
            RETURNING id
            """
        )
        raw_file_id = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO ingestion_runs (raw_file_id, rows_productos_raw)
            VALUES (%s, 3)
            RETURNING id
            """,
            (raw_file_id,),
        )
        run_id = cur.fetchone()[0]

    counts = ProductReconciliation(1, 1, 0, 0, 1, 1)

    with pytest.raises(ValueError, match="do not reconcile"):
        record_product_reconciliation(pg_conn, run_id, counts)


@pytest.mark.parametrize(
    ("cleaning_rows", "key_rows"),
    [(1, 1), (2, 0), (2, 2)],
    ids=["missing-cleaning-count", "missing-key-count", "extra-key-count"],
)
def test_record_product_reconciliation_requires_matching_rejection_counts(
    pg_conn, cleaning_rows, key_rows
):
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO raw_files (filename, url, sha256, size_bytes, fecha_datos)
            VALUES ('counts.zip', 'https://example.test/counts.zip', 'counts', 1, '2026-03-15')
            RETURNING id
            """
        )
        cur.execute(
            "INSERT INTO ingestion_runs (raw_file_id) VALUES (%s) RETURNING id",
            (cur.fetchone()[0],),
        )
        run_id = cur.fetchone()[0]
    add_rejection_counts(
        pg_conn, run_id=run_id, source_file=CLEANING_SOURCE, counts={"a": cleaning_rows}
    )
    add_rejection_counts(
        pg_conn, run_id=run_id, source_file=KEY_RESOLUTION_SOURCE, counts={"b": key_rows}
    )
    counts = ProductReconciliation(
        rows_valid=10,
        rows_rejected=2,
        rows_without_keys=1,
        rows_deduplicated=2,
        rows_fact_pending=7,
        rows_fact_published=7,
    )

    with pytest.raises(ValueError, match="rejection counts do not reconcile"):
        record_product_reconciliation(pg_conn, run_id, counts)


def _run_with_staging_counts(conn, *, source: int, out_of_scope: int | None, raw: int) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO raw_files (filename, url, sha256, size_bytes, fecha_datos)
            VALUES ('scope.zip', 'https://example.test/scope.zip', 'scope', 1, '2026-03-15')
            RETURNING id
            """
        )
        cur.execute(
            """
            INSERT INTO ingestion_runs (
                raw_file_id, rows_productos_source, rows_productos_out_of_scope,
                rows_productos_raw
            )
            VALUES (%s, %s, %s, %s)
            RETURNING id
            """,
            (cur.fetchone()[0], source, out_of_scope, raw),
        )
        return cur.fetchone()[0]


def _all_valid(rows: int, *, is_partial: bool = False) -> ProductReconciliation:
    return ProductReconciliation(
        rows_valid=rows,
        rows_rejected=0,
        rows_without_keys=0,
        rows_deduplicated=0,
        rows_fact_pending=rows,
        rows_fact_published=0 if is_partial else rows,
        is_partial=is_partial,
    )


@pytest.mark.parametrize(
    ("source", "out_of_scope"), [(10, 6), (4, None)], ids=["scoped", "unscoped"]
)
def test_source_rows_reconcile_with_out_of_scope_plus_raw(pg_conn, source, out_of_scope):
    run_id = _run_with_staging_counts(pg_conn, source=source, out_of_scope=out_of_scope, raw=4)

    record_product_reconciliation(pg_conn, run_id, _all_valid(4))


def test_source_rows_missing_from_every_bucket_do_not_reconcile(pg_conn):
    run_id = _run_with_staging_counts(pg_conn, source=11, out_of_scope=6, raw=4)

    with pytest.raises(ValueError, match="out of scope \\+ raw"):
        record_product_reconciliation(pg_conn, run_id, _all_valid(4))


def test_a_partial_run_may_leave_source_rows_out(pg_conn):
    run_id = _run_with_staging_counts(pg_conn, source=11, out_of_scope=6, raw=4)

    record_product_reconciliation(pg_conn, run_id, _all_valid(4, is_partial=True))
