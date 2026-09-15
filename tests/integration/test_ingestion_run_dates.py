from datetime import date

from etl.ingestion_run import open_ingestion_run, record_source_date


def _raw_file_id(conn, fecha_datos: date) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO raw_files (filename, url, sha256, size_bytes, fecha_datos)
            VALUES ('dates.zip', 'https://example.test/dates.zip', 'dates-sha', 1, %s)
            RETURNING id
            """,
            (fecha_datos,),
        )
        return cur.fetchone()[0]


def test_ingestion_run_persists_requested_logical_and_source_dates(pg_conn):
    raw_file_id = _raw_file_id(pg_conn, date(2026, 3, 15))

    run_id = open_ingestion_run(
        pg_conn,
        raw_file_id,
        fecha_solicitada=date(2026, 3, 16),
        fecha_logica=date(2026, 3, 15),
    )
    record_source_date(pg_conn, run_id, date(2026, 3, 14))

    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT fecha_solicitada, fecha_logica, fecha_fuente
            FROM ingestion_runs
            WHERE id = %s
            """,
            (run_id,),
        )
        assert cur.fetchone() == (date(2026, 3, 16), date(2026, 3, 15), date(2026, 3, 14))


def test_ingestion_run_defaults_dates_from_its_raw_file_for_compatibility(pg_conn):
    raw_file_id = _raw_file_id(pg_conn, date(2026, 3, 15))

    run_id = open_ingestion_run(pg_conn, raw_file_id)

    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT fecha_solicitada, fecha_logica, fecha_fuente
            FROM ingestion_runs
            WHERE id = %s
            """,
            (run_id,),
        )
        assert cur.fetchone() == (date(2026, 3, 15), date(2026, 3, 15), None)
