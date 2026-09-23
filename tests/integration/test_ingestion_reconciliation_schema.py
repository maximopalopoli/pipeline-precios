"""Schema coverage for product-row reconciliation metadata."""


def test_ingestion_runs_has_nonnegative_product_reconciliation_counts(pg_conn):
    expected_columns = {
        "rows_productos_valid",
        "rows_productos_rejected",
        "rows_productos_without_keys",
        "rows_productos_deduplicated",
        "rows_fact_pending",
        "rows_fact_published",
    }

    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'ingestion_runs'
            """
        )
        columns = {row[0] for row in cur.fetchall()}

        cur.execute(
            """
            SELECT pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE conname = 'chk_ingestion_reconciliation_counts_nonnegative'
            """
        )
        (constraint,) = cur.fetchone()

    assert expected_columns <= columns
    assert ">= 0" in constraint


def test_ingestion_runs_records_partial_ingestion_metadata(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'ingestion_runs'
              AND column_name IN (
                  'is_partial', 'partial_chain_keys', 'partial_max_product_rows'
              )
            """
        )
        columns = {row[0] for row in cur.fetchall()}
        cur.execute(
            """
            SELECT pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE conname = 'chk_partial_ingestion_configuration'
            """
        )
        (constraint,) = cur.fetchone()

    assert columns == {
        "is_partial",
        "partial_chain_keys",
        "partial_max_product_rows",
    }
    assert "partial_max_product_rows > 0" in constraint
