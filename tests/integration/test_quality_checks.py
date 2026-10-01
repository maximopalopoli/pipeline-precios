"""
Integration tests for etl/quality/checks.py.

Each test loads the sample.zip into staging and then exercises one
quality check in isolation, either via run_all() or the individual
check functions.

sample.zip fixture content (2 inner ZIPs):
  sepa_1_comercio-sepa-1: 1 comercio (id=1, bandera=1), 2 sucursales, 4 productos
  sepa_1_comercio-sepa-2: 1 comercio (id=1, bandera=2), 1 sucursal,  3 productos
  ─────────────────────────────────────────────────────────────────────────────
  Total:                   2 comercios, 3 sucursales, 7 productos
"""


import pytest

from etl.staging.loader import load
from etl.quality.checks import (
    QualityCheckError,
    Severity,
    check_coordinates_bbox,
    check_fk_integrity,
    check_min_row_count,
    check_pk_uniqueness,
    check_positive_prices,
    check_provincia,
    check_required_nulls,
    check_row_count_drift,
    check_schema,
    check_tipo_sucursal,
    run_all,
)

# ── Helpers ────────────────────────────────────────────────────────────────────


_raw_file_counter = 0


def _insert_raw_file(conn) -> int:
    global _raw_file_counter
    _raw_file_counter += 1
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO raw_files (filename, url, sha256, size_bytes, fecha_datos)
            VALUES ('sample.zip', 'http://test', %s, 0, '2026-03-17')
            RETURNING id
            """,
            (f"deadbeef{_raw_file_counter}",),
        )
        fid = cur.fetchone()[0]
    conn.commit()
    return fid


def _insert_run(conn, *, raw_file_id: int) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ingestion_runs (raw_file_id) VALUES (%s) RETURNING id",
            (raw_file_id,),
        )
        run_id = cur.fetchone()[0]
    conn.commit()
    return run_id


def _load_sample(conn, sample_zip_path) -> int:
    """Insert a run, load sample.zip into staging, return run_id."""
    raw_file_id = _insert_raw_file(conn)
    run_id = _insert_run(conn, raw_file_id=raw_file_id)
    load(conn, sample_zip_path, run_id)
    return run_id


# ── Schema check ───────────────────────────────────────────────────────────────


def test_check_schema_passes_on_clean_db(pg_conn, sample_zip_path):
    run_id = _load_sample(pg_conn, sample_zip_path)
    result = check_schema(pg_conn, run_id)
    assert result.passed
    assert result.severity == Severity.CRITICAL
    assert result.details["missing_cols"] == {}
    assert result.details["extra_cols"] == {}


# ── PK uniqueness ──────────────────────────────────────────────────────────────


def test_check_pk_uniqueness_passes_on_sample(pg_conn, sample_zip_path):
    run_id = _load_sample(pg_conn, sample_zip_path)
    result = check_pk_uniqueness(pg_conn, run_id)
    assert result.passed
    assert result.severity == Severity.WARNING


def test_check_pk_uniqueness_detects_duplicate_comercio(pg_conn, sample_zip_path):
    run_id = _load_sample(pg_conn, sample_zip_path)

    # Inject a duplicate comercio row
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO stg_comercios (run_id, id_comercio, id_bandera,
                comercio_cuit, comercio_razon_social, comercio_bandera_nombre,
                comercio_bandera_url, comercio_ultima_actualizacion,
                comercio_version_sepa)
            SELECT run_id, id_comercio, id_bandera,
                comercio_cuit, comercio_razon_social, comercio_bandera_nombre,
                comercio_bandera_url, comercio_ultima_actualizacion,
                comercio_version_sepa
              FROM stg_comercios
             WHERE run_id = %s
             LIMIT 1
            """,
            (run_id,),
        )
    pg_conn.commit()

    result = check_pk_uniqueness(pg_conn, run_id)
    assert not result.passed
    assert "stg_comercios" in result.details["duplicate_rows"]


def test_check_pk_uniqueness_detects_duplicate_producto(pg_conn, sample_zip_path):
    run_id = _load_sample(pg_conn, sample_zip_path)

    with pg_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO stg_productos (run_id, id_comercio, id_bandera, id_sucursal,
                id_producto, productos_ean, productos_descripcion,
                productos_cantidad_presentacion, productos_unidad_medida_presentacion,
                productos_marca, productos_precio_lista, productos_precio_referencia,
                productos_cantidad_referencia, productos_unidad_medida_referencia,
                productos_precio_unitario_promo1, productos_leyenda_promo1,
                productos_precio_unitario_promo2, productos_leyenda_promo2)
            SELECT run_id, id_comercio, id_bandera, id_sucursal,
                id_producto, productos_ean, productos_descripcion,
                productos_cantidad_presentacion, productos_unidad_medida_presentacion,
                productos_marca, productos_precio_lista, productos_precio_referencia,
                productos_cantidad_referencia, productos_unidad_medida_referencia,
                productos_precio_unitario_promo1, productos_leyenda_promo1,
                productos_precio_unitario_promo2, productos_leyenda_promo2
              FROM stg_productos
             WHERE run_id = %s
             LIMIT 1
            """,
            (run_id,),
        )
    pg_conn.commit()

    result = check_pk_uniqueness(pg_conn, run_id)
    assert not result.passed
    assert result.details["duplicate_rows"]["stg_productos"] == 1


# ── FK integrity ───────────────────────────────────────────────────────────────


def test_check_fk_integrity_passes_on_sample(pg_conn, sample_zip_path):
    run_id = _load_sample(pg_conn, sample_zip_path)
    result = check_fk_integrity(pg_conn, run_id)
    assert result.passed
    assert result.severity == Severity.WARNING


def test_check_fk_integrity_detects_orphan_producto_comercio(pg_conn, sample_zip_path):
    run_id = _load_sample(pg_conn, sample_zip_path)

    # Remove all comercios so products have no parent
    with pg_conn.cursor() as cur:
        cur.execute("DELETE FROM stg_comercios WHERE run_id = %s", (run_id,))
    pg_conn.commit()

    result = check_fk_integrity(pg_conn, run_id)
    assert not result.passed
    assert result.details["violations"]["productos_sin_comercio"] > 0


def test_check_fk_integrity_detects_orphan_producto_sucursal(pg_conn, sample_zip_path):
    run_id = _load_sample(pg_conn, sample_zip_path)

    # Remove all sucursales so products have no parent branch
    with pg_conn.cursor() as cur:
        cur.execute("DELETE FROM stg_sucursales WHERE run_id = %s", (run_id,))
    pg_conn.commit()

    result = check_fk_integrity(pg_conn, run_id)
    assert not result.passed
    assert result.details["violations"]["productos_sin_sucursal"] > 0


# ── Positive prices ────────────────────────────────────────────────────────────


def test_check_positive_prices_passes_on_sample(pg_conn, sample_zip_path):
    run_id = _load_sample(pg_conn, sample_zip_path)
    result = check_positive_prices(pg_conn, run_id)
    assert result.passed
    assert result.severity == Severity.WARNING


def test_check_positive_prices_detects_zero_price(pg_conn, sample_zip_path):
    run_id = _load_sample(pg_conn, sample_zip_path)

    with pg_conn.cursor() as cur:
        cur.execute(
            """
            UPDATE stg_productos
               SET productos_precio_lista = '0'
             WHERE ctid = (
                 SELECT ctid FROM stg_productos WHERE run_id = %s LIMIT 1
             )
            """,
            (run_id,),
        )
    pg_conn.commit()

    result = check_positive_prices(pg_conn, run_id)
    assert not result.passed
    assert result.details["bad_precio_lista_rows"] >= 1


def test_check_positive_prices_detects_negative_price(pg_conn, sample_zip_path):
    run_id = _load_sample(pg_conn, sample_zip_path)

    with pg_conn.cursor() as cur:
        cur.execute(
            """
            UPDATE stg_productos
               SET productos_precio_lista = '-5.99'
             WHERE ctid = (
                 SELECT ctid FROM stg_productos WHERE run_id = %s LIMIT 1
             )
            """,
            (run_id,),
        )
    pg_conn.commit()

    result = check_positive_prices(pg_conn, run_id)
    assert not result.passed
    assert result.details["bad_precio_lista_rows"] >= 1


def test_check_positive_prices_detects_empty_price(pg_conn, sample_zip_path):
    run_id = _load_sample(pg_conn, sample_zip_path)

    with pg_conn.cursor() as cur:
        cur.execute(
            """
            UPDATE stg_productos
               SET productos_precio_lista = ''
             WHERE ctid = (
                 SELECT ctid FROM stg_productos WHERE run_id = %s LIMIT 1
             )
            """,
            (run_id,),
        )
    pg_conn.commit()

    result = check_positive_prices(pg_conn, run_id)
    assert not result.passed


# ── Minimum row count ──────────────────────────────────────────────────────────


def test_check_min_row_count_passes_when_above_minimum(pg_conn, sample_zip_path):
    run_id = _load_sample(pg_conn, sample_zip_path)
    # sample has 7 productos; set min=5 to pass
    result = check_min_row_count(pg_conn, run_id, min_productos=5)
    assert result.passed
    assert result.severity == Severity.CRITICAL
    assert result.details["actual_rows"] == 7


def test_check_min_row_count_fails_when_below_minimum(pg_conn, sample_zip_path):
    run_id = _load_sample(pg_conn, sample_zip_path)
    # sample has 7 productos; require more than that
    result = check_min_row_count(pg_conn, run_id, min_productos=8)
    assert not result.passed
    assert result.details["actual_rows"] < result.details["min_required"]


def test_check_min_row_count_fails_on_empty_staging(pg_conn, sample_zip_path):
    run_id = _load_sample(pg_conn, sample_zip_path)

    with pg_conn.cursor() as cur:
        cur.execute("DELETE FROM stg_productos WHERE run_id = %s", (run_id,))
    pg_conn.commit()

    result = check_min_row_count(pg_conn, run_id, min_productos=1)
    assert not result.passed
    assert result.details["actual_rows"] == 0


# ── run_all ────────────────────────────────────────────────────────────────────


def test_run_all_passes_on_clean_sample(pg_conn, sample_zip_path):
    run_id = _load_sample(pg_conn, sample_zip_path)
    results = run_all(pg_conn, run_id, min_productos=5)
    assert all(r.passed for r in results)
    assert len(results) == 10


def test_run_all_raises_on_critical_failure(pg_conn, sample_zip_path):
    run_id = _load_sample(pg_conn, sample_zip_path)

    # Wipe productos → triggers min_row_count and fk_integrity failures
    with pg_conn.cursor() as cur:
        cur.execute("DELETE FROM stg_productos WHERE run_id = %s", (run_id,))
    pg_conn.commit()

    with pytest.raises(QualityCheckError) as exc_info:
        run_all(pg_conn, run_id, min_productos=1)

    assert len(exc_info.value.failures) > 0
    assert all(f.severity == Severity.CRITICAL for f in exc_info.value.failures)


def test_run_all_does_not_raise_on_warning_only(pg_conn, sample_zip_path):
    run_id = _load_sample(pg_conn, sample_zip_path)

    # Introduce a bad price (WARNING severity)
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            UPDATE stg_productos
               SET productos_precio_lista = '0'
             WHERE ctid = (
                 SELECT ctid FROM stg_productos WHERE run_id = %s LIMIT 1
             )
            """,
            (run_id,),
        )
    pg_conn.commit()

    # Should not raise; positive_prices is WARNING
    results = run_all(pg_conn, run_id, min_productos=5)
    failed = [r for r in results if not r.passed]
    assert len(failed) == 1
    assert failed[0].name == "positive_prices"
    assert failed[0].severity == Severity.WARNING


# ── Row count drift ────────────────────────────────────────────────────────────


def test_check_row_count_drift_passes_with_no_previous_run(pg_conn, sample_zip_path):
    run_id = _load_sample(pg_conn, sample_zip_path)
    result = check_row_count_drift(pg_conn, run_id)
    assert result.passed
    assert result.severity == Severity.WARNING
    assert result.details["previous_rows"] is None


def test_check_row_count_drift_passes_within_threshold(pg_conn, sample_zip_path):
    # First run: load sample and mark completed
    run_id_1 = _load_sample(pg_conn, sample_zip_path)
    with pg_conn.cursor() as cur:
        cur.execute(
            "UPDATE ingestion_runs SET status='completed', rows_productos_raw=7 WHERE id=%s",
            (run_id_1,),
        )
    pg_conn.commit()

    # Second run with the same count — drift = 0
    run_id_2 = _load_sample(pg_conn, sample_zip_path)
    result = check_row_count_drift(pg_conn, run_id_2, max_drift=0.20)
    assert result.passed
    assert result.details["drift"] == 0.0


def test_check_row_count_drift_fails_beyond_threshold(pg_conn, sample_zip_path):
    # First run with a large count
    run_id_1 = _load_sample(pg_conn, sample_zip_path)
    with pg_conn.cursor() as cur:
        cur.execute(
            "UPDATE ingestion_runs SET status='completed', rows_productos_raw=1000 WHERE id=%s",
            (run_id_1,),
        )
    pg_conn.commit()

    # Second run has only 7 rows — drift ≈ 99.3 %
    run_id_2 = _load_sample(pg_conn, sample_zip_path)
    result = check_row_count_drift(pg_conn, run_id_2, max_drift=0.20)
    assert not result.passed
    assert result.details["drift"] > 0.20


def test_check_row_count_drift_compares_runs_of_the_same_scope(pg_conn, sample_zip_path):
    national = _load_sample(pg_conn, sample_zip_path)
    scoped = _load_sample(pg_conn, sample_zip_path)
    with pg_conn.cursor() as cur:
        cur.execute(
            "UPDATE ingestion_runs SET status='completed', rows_productos_raw=1000 WHERE id=%s",
            (national,),
        )
        cur.execute(
            """UPDATE ingestion_runs
               SET status='completed', rows_productos_raw=7, regional_scope='amba'
               WHERE id=%s""",
            (scoped,),
        )
    pg_conn.commit()

    run_id = _load_sample(pg_conn, sample_zip_path)
    with pg_conn.cursor() as cur:
        cur.execute(
            "UPDATE ingestion_runs SET regional_scope='amba' WHERE id=%s", (run_id,)
        )
    pg_conn.commit()
    result = check_row_count_drift(pg_conn, run_id, max_drift=0.20)
    assert result.passed
    assert result.details["previous_rows"] == 7


def test_run_all_reads_limits_from_the_environment(pg_conn, sample_zip_path, monkeypatch):
    run_id = _load_sample(pg_conn, sample_zip_path)
    monkeypatch.setenv("SEPA_MIN_PRODUCT_ROWS", "8")
    with pytest.raises(QualityCheckError) as exc_info:
        run_all(pg_conn, run_id)
    assert [failure.name for failure in exc_info.value.failures] == ["min_row_count"]


# ── Required nulls ─────────────────────────────────────────────────────────────


def test_check_required_nulls_passes_on_sample(pg_conn, sample_zip_path):
    run_id = _load_sample(pg_conn, sample_zip_path)
    result = check_required_nulls(pg_conn, run_id)
    assert result.passed
    assert result.severity == Severity.WARNING


def test_check_required_nulls_detects_empty_required_field(pg_conn, sample_zip_path):
    run_id = _load_sample(pg_conn, sample_zip_path)

    with pg_conn.cursor() as cur:
        cur.execute(
            """
            UPDATE stg_comercios
               SET comercio_cuit = ''
             WHERE ctid = (
                 SELECT ctid FROM stg_comercios WHERE run_id = %s LIMIT 1
             )
            """,
            (run_id,),
        )
    pg_conn.commit()

    result = check_required_nulls(pg_conn, run_id)
    assert not result.passed
    assert result.details["nulls_by_table"]["stg_comercios"]["comercio_cuit"] == 1


# ── Coordinates bounding box ───────────────────────────────────────────────────


def test_check_coordinates_bbox_passes_on_sample(pg_conn, sample_zip_path):
    """sample.zip sucursales have empty coordinates — check passes (no coords to validate)."""
    run_id = _load_sample(pg_conn, sample_zip_path)
    result = check_coordinates_bbox(pg_conn, run_id)
    assert result.passed
    assert result.severity == Severity.WARNING


def test_check_coordinates_bbox_passes_with_valid_coords(pg_conn, sample_zip_path):
    run_id = _load_sample(pg_conn, sample_zip_path)

    # Buenos Aires city centre — well within bbox
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            UPDATE stg_sucursales
               SET sucursales_latitud = '-34.6037', sucursales_longitud = '-58.3816'
             WHERE ctid = (SELECT ctid FROM stg_sucursales WHERE run_id = %s LIMIT 1)
            """,
            (run_id,),
        )
    pg_conn.commit()

    result = check_coordinates_bbox(pg_conn, run_id)
    assert result.passed


def test_check_coordinates_bbox_detects_out_of_bounds(pg_conn, sample_zip_path):
    run_id = _load_sample(pg_conn, sample_zip_path)

    # Coords for Madrid, Spain — clearly outside Argentina
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            UPDATE stg_sucursales
               SET sucursales_latitud = '40.4168', sucursales_longitud = '-3.7038'
             WHERE ctid = (SELECT ctid FROM stg_sucursales WHERE run_id = %s LIMIT 1)
            """,
            (run_id,),
        )
    pg_conn.commit()

    result = check_coordinates_bbox(pg_conn, run_id)
    assert not result.passed
    assert result.details["out_of_bbox"] >= 1


# ── Tipo sucursal ──────────────────────────────────────────────────────────────


def test_check_tipo_sucursal_passes_on_sample(pg_conn, sample_zip_path):
    run_id = _load_sample(pg_conn, sample_zip_path)
    result = check_tipo_sucursal(pg_conn, run_id)
    assert result.passed
    assert result.severity == Severity.WARNING


def test_check_tipo_sucursal_detects_invalid_type(pg_conn, sample_zip_path):
    run_id = _load_sample(pg_conn, sample_zip_path)

    with pg_conn.cursor() as cur:
        cur.execute(
            """
            UPDATE stg_sucursales
               SET sucursales_tipo = 'Megamercado'
             WHERE ctid = (SELECT ctid FROM stg_sucursales WHERE run_id = %s LIMIT 1)
            """,
            (run_id,),
        )
    pg_conn.commit()

    result = check_tipo_sucursal(pg_conn, run_id)
    assert not result.passed
    assert "Megamercado" in result.details["invalid_tipos"]


# ── Provincia ──────────────────────────────────────────────────────────────────


def test_check_provincia_passes_on_sample(pg_conn, sample_zip_path):
    run_id = _load_sample(pg_conn, sample_zip_path)
    result = check_provincia(pg_conn, run_id)
    assert result.passed
    assert result.severity == Severity.WARNING


def test_check_provincia_detects_invalid_code(pg_conn, sample_zip_path):
    run_id = _load_sample(pg_conn, sample_zip_path)

    with pg_conn.cursor() as cur:
        cur.execute(
            """
            UPDATE stg_sucursales
               SET sucursales_provincia = 'AR-ZZ'
             WHERE ctid = (SELECT ctid FROM stg_sucursales WHERE run_id = %s LIMIT 1)
            """,
            (run_id,),
        )
    pg_conn.commit()

    result = check_provincia(pg_conn, run_id)
    assert not result.passed
    assert "AR-ZZ" in result.details["invalid_provincias"]


def test_check_provincia_detects_non_ar_code(pg_conn, sample_zip_path):
    run_id = _load_sample(pg_conn, sample_zip_path)

    with pg_conn.cursor() as cur:
        cur.execute(
            """
            UPDATE stg_sucursales
               SET sucursales_provincia = 'Buenos Aires'
             WHERE ctid = (SELECT ctid FROM stg_sucursales WHERE run_id = %s LIMIT 1)
            """,
            (run_id,),
        )
    pg_conn.commit()

    result = check_provincia(pg_conn, run_id)
    assert not result.passed
    assert "Buenos Aires" in result.details["invalid_provincias"]


# ── run_all covers all 10 checks ───────────────────────────────────────────────


def test_run_all_returns_ten_results(pg_conn, sample_zip_path):
    run_id = _load_sample(pg_conn, sample_zip_path)
    results = run_all(pg_conn, run_id, min_productos=5)
    assert len(results) == 10
