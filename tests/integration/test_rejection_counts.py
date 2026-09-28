"""Complete rejection counts per run, source and reason."""

import psycopg
import pytest

from etl.rejection_counts import add_rejection_counts, rejection_counts_for_run


def _create_run(conn) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO raw_files (filename, url, sha256, size_bytes, fecha_datos)
            VALUES ('data.zip', 'http://example.test/data.zip', 'abc', 10, '2026-03-17')
            RETURNING id
            """
        )
        cur.execute(
            "INSERT INTO ingestion_runs (raw_file_id, status) VALUES (%s, 'running') RETURNING id",
            (cur.fetchone()[0],),
        )
        run_id = cur.fetchone()[0]
    conn.commit()
    return run_id


def test_counts_accumulate_across_chunks(pg_conn):
    run_id = _create_run(pg_conn)

    add_rejection_counts(
        pg_conn,
        run_id=run_id,
        source_file="clean_productos",
        counts={"invalid_price": 3, "missing_ean": 1},
    )
    added = add_rejection_counts(
        pg_conn,
        run_id=run_id,
        source_file="clean_productos",
        counts={"invalid_price": 2},
    )
    add_rejection_counts(
        pg_conn,
        run_id=run_id,
        source_file="fact_key_resolution",
        counts={"invalid_price": 4},
    )
    pg_conn.commit()

    assert added == 2
    assert rejection_counts_for_run(pg_conn, run_id) == {
        ("clean_productos", "invalid_price"): 5,
        ("clean_productos", "missing_ean"): 1,
        ("fact_key_resolution", "invalid_price"): 4,
    }


def test_zero_counts_are_not_stored(pg_conn):
    run_id = _create_run(pg_conn)

    added = add_rejection_counts(
        pg_conn, run_id=run_id, source_file="clean_productos", counts={"x": 0}
    )

    assert added == 0
    assert rejection_counts_for_run(pg_conn, run_id) == {}


def test_negative_counts_are_rejected(pg_conn):
    run_id = _create_run(pg_conn)

    with pytest.raises(ValueError, match="nonnegative"):
        add_rejection_counts(
            pg_conn,
            run_id=run_id,
            source_file="clean_productos",
            counts={"ok": 1, "bad": -1},
        )
    assert rejection_counts_for_run(pg_conn, run_id) == {}


def test_counts_belong_to_an_existing_run(pg_conn):
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        add_rejection_counts(
            pg_conn, run_id=999_999, source_file="clean_productos", counts={"x": 1}
        )
    pg_conn.rollback()


def test_the_schema_rejects_a_nonpositive_count(pg_conn):
    run_id = _create_run(pg_conn)

    with pytest.raises(psycopg.errors.CheckViolation):
        with pg_conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO rejection_counts (run_id, source_file, rejection_reason, row_count)
                VALUES (%s, 'clean_productos', 'x', 0)
                """,
                (run_id,),
            )
    pg_conn.rollback()
