"""Complete rejection counts per run, source and reason."""

import psycopg
import pytest

from etl.reconciliation import record_product_rejections
from etl.rejection_counts import (
    DEFAULT_SAMPLE_ROWS,
    add_rejection_counts,
    record_rejections,
    rejection_counts_for_run,
    sample_rows_from_environment,
)


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


def _sampled_rows(conn, run_id: int) -> list[tuple[str, str]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT source_file, rejection_reason
            FROM rejected_rows
            WHERE run_id = %s
            ORDER BY id
            """,
            (run_id,),
        )
        return cur.fetchall()


def _product_rejections(code: str, rows: int) -> list[dict]:
    return [
        {"row": {"id_producto": str(i)}, "code": code, "reason": f"{code} #{i}"}
        for i in range(rows)
    ]


def test_every_rejection_is_counted_but_only_a_sample_is_stored(pg_conn):
    run_id = _create_run(pg_conn)

    for _chunk in range(3):
        record_product_rejections(
            pg_conn,
            run_id=run_id,
            source="clean_productos",
            rejected=_product_rejections("invalid_price", 4)
            + _product_rejections("missing_ean", 1),
            sample_rows=5,
        )
    pg_conn.commit()

    assert rejection_counts_for_run(pg_conn, run_id) == {
        ("clean_productos", "invalid_price"): 12,
        ("clean_productos", "missing_ean"): 3,
    }
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT rejection_code, COUNT(*)
            FROM rejected_rows WHERE run_id = %s
            GROUP BY rejection_code ORDER BY rejection_code
            """,
            (run_id,),
        )
        assert cur.fetchall() == [("invalid_price", 5), ("missing_ean", 3)]
    reasons = [reason for _source, reason in _sampled_rows(pg_conn, run_id)]
    assert reasons == [
        "invalid_price #0",
        "invalid_price #1",
        "invalid_price #2",
        "invalid_price #3",
        "missing_ean #0",
        "invalid_price #0",
        "missing_ean #0",
        "missing_ean #0",
    ]


def test_samples_are_bounded_per_source(pg_conn):
    run_id = _create_run(pg_conn)

    for source in ("clean_productos", "fact_key_resolution"):
        record_product_rejections(
            pg_conn,
            run_id=run_id,
            source=source,
            rejected=_product_rejections("x", 3),
            sample_rows=2,
        )
    pg_conn.commit()

    assert [source for source, _reason in _sampled_rows(pg_conn, run_id)] == [
        "clean_productos",
        "clean_productos",
        "fact_key_resolution",
        "fact_key_resolution",
    ]


def test_a_zero_sample_keeps_only_counts(pg_conn):
    run_id = _create_run(pg_conn)

    counted = record_product_rejections(
        pg_conn,
        run_id=run_id,
        source="clean_productos",
        rejected=_product_rejections("x", 3),
        sample_rows=0,
    )

    assert counted == 3
    assert rejection_counts_for_run(pg_conn, run_id) == {("clean_productos", "x"): 3}
    assert _sampled_rows(pg_conn, run_id) == []


def test_a_sampled_row_must_be_counted(pg_conn):
    run_id = _create_run(pg_conn)

    with pytest.raises(ValueError, match="is not counted"):
        record_rejections(
            pg_conn,
            run_id=run_id,
            source_file="clean_productos",
            counts={"x": 1},
            samples=[("y", "raw", "reason")],
            sample_rows=5,
        )


@pytest.mark.parametrize(
    ("configured", "expected"),
    [(None, DEFAULT_SAMPLE_ROWS), ("", DEFAULT_SAMPLE_ROWS), (" 25 ", 25), ("0", 0)],
)
def test_sample_size_comes_from_the_environment(configured, expected):
    env = {} if configured is None else {"SEPA_REJECTED_SAMPLE_ROWS": configured}

    assert sample_rows_from_environment(env) == expected


@pytest.mark.parametrize("configured", ["-1", "many", "1.5"])
def test_an_invalid_sample_size_fails_fast(configured):
    with pytest.raises(ValueError, match="SEPA_REJECTED_SAMPLE_ROWS"):
        sample_rows_from_environment({"SEPA_REJECTED_SAMPLE_ROWS": configured})
