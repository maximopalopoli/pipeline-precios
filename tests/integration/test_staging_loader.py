"""
Integration test: load sample.zip into staging and verify counts.

sample.zip contains 2 inner ZIPs:
  - sepa_1_comercio-sepa-1: 1 comercio, 2 sucursales, 4 productos
  - sepa_1_comercio-sepa-2: 1 comercio, 1 sucursal,  3 productos
  ───────────────────────────────────────────────────────────────
  Total:                     2 comercios, 3 sucursales, 7 productos
"""

import csv
import io
import zipfile


from etl.development_ingestion import DevelopmentIngestionPolicy
from etl.staging.loader import _PRODUCTOS_COLS, LoadResult, load, register_counts

# ── Expected counts from sample.zip ───────────────────────────────────────────

EXPECTED_INNER_ZIPS = 2
EXPECTED_COMERCIOS = 2
EXPECTED_SUCURSALES = 3
EXPECTED_PRODUCTOS = 7


# ── Helpers ───────────────────────────────────────────────────────────────────


def _insert_run(conn, *, raw_file_id: int) -> int:
    """Insert a minimal ingestion_runs row and return its id."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ingestion_runs (raw_file_id) VALUES (%s) RETURNING id",
            (raw_file_id,),
        )
        run_id = cur.fetchone()[0]
    conn.commit()
    return run_id


def _insert_raw_file(conn) -> int:
    """Insert a dummy raw_files row and return its id."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO raw_files (filename, url, sha256, size_bytes, fecha_datos)
            VALUES ('sample.zip', 'http://test', 'deadbeef', 0, '2026-03-17')
            RETURNING id
            """,
        )
        file_id = cur.fetchone()[0]
    conn.commit()
    return file_id


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_load_returns_correct_counts(pg_conn, sample_zip_path):
    raw_file_id = _insert_raw_file(pg_conn)
    run_id = _insert_run(pg_conn, raw_file_id=raw_file_id)
    heartbeats: list[None] = []

    result = load(
        pg_conn,
        sample_zip_path,
        run_id,
        heartbeat=lambda: heartbeats.append(None),
    )

    assert isinstance(result, LoadResult)
    assert result.inner_zips_count == EXPECTED_INNER_ZIPS
    assert result.rows_comercios == EXPECTED_COMERCIOS
    assert result.rows_sucursales == EXPECTED_SUCURSALES
    assert result.rows_productos == EXPECTED_PRODUCTOS
    assert result.rows_productos_source == EXPECTED_PRODUCTOS
    assert result.rows_productos_selected == EXPECTED_PRODUCTOS
    assert len(heartbeats) == EXPECTED_INNER_ZIPS


def test_load_inserts_rows_into_staging(pg_conn, sample_zip_path):
    raw_file_id = _insert_raw_file(pg_conn)
    run_id = _insert_run(pg_conn, raw_file_id=raw_file_id)

    load(pg_conn, sample_zip_path, run_id)

    with pg_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM stg_comercios WHERE run_id = %s", (run_id,))
        assert cur.fetchone()[0] == EXPECTED_COMERCIOS

        cur.execute("SELECT COUNT(*) FROM stg_sucursales WHERE run_id = %s", (run_id,))
        assert cur.fetchone()[0] == EXPECTED_SUCURSALES

        cur.execute("SELECT COUNT(*) FROM stg_productos WHERE run_id = %s", (run_id,))
        assert cur.fetchone()[0] == EXPECTED_PRODUCTOS


def test_load_is_idempotent(pg_conn, sample_zip_path):
    """Running load() twice for the same run_id must not duplicate rows."""
    raw_file_id = _insert_raw_file(pg_conn)
    run_id = _insert_run(pg_conn, raw_file_id=raw_file_id)

    load(pg_conn, sample_zip_path, run_id)
    load(pg_conn, sample_zip_path, run_id)  # second run, same run_id

    with pg_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM stg_comercios WHERE run_id = %s", (run_id,))
        assert cur.fetchone()[0] == EXPECTED_COMERCIOS

        cur.execute("SELECT COUNT(*) FROM stg_sucursales WHERE run_id = %s", (run_id,))
        assert cur.fetchone()[0] == EXPECTED_SUCURSALES

        cur.execute("SELECT COUNT(*) FROM stg_productos WHERE run_id = %s", (run_id,))
        assert cur.fetchone()[0] == EXPECTED_PRODUCTOS


def test_load_applies_explicit_chain_and_product_bounds_deterministically(
    pg_conn, sample_zip_path
):
    raw_file_id = _insert_raw_file(pg_conn)
    run_id = _insert_run(pg_conn, raw_file_id=raw_file_id)

    result = load(
        pg_conn,
        sample_zip_path,
        run_id,
        development_policy=DevelopmentIngestionPolicy(
            chain_keys=((1, 1),), max_product_rows=2
        ),
    )

    assert result == LoadResult(
        inner_zips_count=1,
        rows_comercios=1,
        rows_sucursales=2,
        rows_productos=2,
        rows_productos_source=4,
        rows_productos_selected=4,
    )
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT id_comercio, id_bandera FROM stg_productos WHERE run_id = %s",
            (run_id,),
        )
        assert cur.fetchall() == [("1", "1")]
        cur.execute(
            "SELECT id_producto FROM stg_productos WHERE run_id = %s ORDER BY id_producto",
            (run_id,),
        )
        assert cur.fetchall() == [("7790895000107",), ("7793940001111",)]


def test_register_counts_updates_ingestion_run(pg_conn, sample_zip_path):
    raw_file_id = _insert_raw_file(pg_conn)
    run_id = _insert_run(pg_conn, raw_file_id=raw_file_id)

    result = load(pg_conn, sample_zip_path, run_id)
    register_counts(pg_conn, run_id, result)

    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT inner_zips_count, rows_comercios, rows_sucursales,
                   rows_productos_source, rows_productos_raw,
                   rows_productos_selected
              FROM ingestion_runs
             WHERE id = %s
            """,
            (run_id,),
        )
        row = cur.fetchone()

    assert row == (
        EXPECTED_INNER_ZIPS,
        EXPECTED_COMERCIOS,
        EXPECTED_SUCURSALES,
        EXPECTED_PRODUCTOS,
        EXPECTED_PRODUCTOS,
        EXPECTED_PRODUCTOS,
    )


def test_register_counts_keeps_source_selection_outside_staged_raw_rows(
    pg_conn, sample_zip_path
):
    raw_file_id = _insert_raw_file(pg_conn)
    run_id = _insert_run(pg_conn, raw_file_id=raw_file_id)

    result = load(
        pg_conn,
        sample_zip_path,
        run_id,
        development_policy=DevelopmentIngestionPolicy(
            chain_keys=((1, 1),), max_product_rows=2
        ),
    )
    register_counts(pg_conn, run_id, result)

    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT rows_productos_source, rows_productos_selected, rows_productos_raw
              FROM ingestion_runs
             WHERE id = %s
            """,
            (run_id,),
        )

        assert cur.fetchone() == (4, 4, 2)


def test_load_streams_quoted_newlines_and_strips_nul_bytes(pg_conn, tmp_path):
    raw_file_id = _insert_raw_file(pg_conn)
    run_id = _insert_run(pg_conn, raw_file_id=raw_file_id)
    product_csv = io.StringIO()
    writer = csv.writer(product_csv, delimiter="|", lineterminator="\n")
    writer.writerow(_PRODUCTOS_COLS)
    writer.writerow(
        [
            "1",
            "1",
            "1",
            "sku-1",
            "7790000000001",
            "Descripcion con\nsalto de linea",
            "1",
            "un",
            "Marca\x00 limpia",
            "10",
            "10",
            "1",
            "un",
            "",
            "",
            "",
            "",
        ]
    )
    writer.writerow(["Ultima actualizacion"])

    archive_path = tmp_path / "quoted-values.zip"
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as inner_zip:
        inner_zip.writestr("productos.csv", product_csv.getvalue())
    with zipfile.ZipFile(archive_path, "w") as outer_zip:
        outer_zip.writestr("sepa_1_comercio-sepa-1_test.zip", inner.getvalue())

    result = load(pg_conn, archive_path, run_id)

    assert result.rows_productos == 1
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT productos_descripcion, productos_marca
              FROM stg_productos
             WHERE run_id = %s
            """,
            (run_id,),
        )
        assert cur.fetchone() == ("Descripcion con\nsalto de linea", "Marca limpia")
