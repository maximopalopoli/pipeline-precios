"""A product's attributes are chosen by a vote of the chains that sell it."""

from decimal import Decimal

import pytest

from etl.load.warehouse_loader import load_dim_produto_from_staging

EAN = "7791234567898"


@pytest.fixture()
def run_id(pg_conn) -> int:
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO raw_files (filename, url, sha256, size_bytes, fecha_datos)
            VALUES ('test.zip', 'http://test', 'canonical', 1, '2026-03-24')
            RETURNING id
            """
        )
        raw_file_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO ingestion_runs (raw_file_id, status) VALUES (%s, 'running') RETURNING id",
            (raw_file_id,),
        )
        return cur.fetchone()[0]


def _stage(pg_conn, run_id, rows):
    """Stage rows of (chain, store, description, brand, quantity, unit)."""
    with pg_conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO stg_productos (
                run_id, id_comercio, id_bandera, id_sucursal, id_producto,
                productos_ean, productos_descripcion, productos_marca,
                productos_cantidad_presentacion, productos_unidad_medida_presentacion,
                productos_cantidad_referencia, productos_unidad_medida_referencia
            ) VALUES (%s, %s, '1', %s, %s, '1', %s, %s, %s, %s, '1', 'KG')
            """,
            [(run_id, chain, store, EAN, *attributes) for chain, store, *attributes in rows],
        )


def _product(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT productos_descripcion, productos_marca,
                   productos_cantidad_presentacion, productos_unidad_medida_presentacion,
                   contenido, contenido_unidad, cadenas_fuente
            FROM dim_producto WHERE producto_identity = %s
            """,
            (f"ean:{EAN}",),
        )
        return cur.fetchone()


def _conflicts(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT atributo, valores FROM producto_conflictos ORDER BY atributo"
        )
        return cur.fetchall()


@pytest.mark.parametrize(
    "cantidad, unidad, amount, unit",
    [
        ("20", "GR", Decimal("20"), "g"),
        ("20.00", "gr.", Decimal("20"), "g"),
        ("0.02", "KG", Decimal("20"), "g"),
        (".5", "LT", Decimal("500"), "ml"),
        ("100", "CM3", Decimal("100"), "ml"),
        ("75", "CL", Decimal("750"), "ml"),
        ("6", "UNI", Decimal("6"), "u"),
        ("1", "EA", Decimal("1"), "u"),
        ("2", "MT", None, None),
        ("0", "GR", None, None),
        ("abc", "GR", None, None),
        ("", "", None, None),
    ],
)
def test_package_size_is_normalized_to_a_base_unit(pg_conn, cantidad, unidad, amount, unit):
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT pipeline_size_amount(%s, %s),
                   CASE WHEN pipeline_size_amount(%s, %s) IS NOT NULL
                        THEN pipeline_size_unit(%s) END
            """,
            (cantidad, unidad, cantidad, unidad, unidad),
        )
        assert cur.fetchone() == (amount, unit)


def test_each_chain_votes_once_whatever_its_store_count(pg_conn, run_id):
    _stage(
        pg_conn,
        run_id,
        [
            # Chain 15 has many stores, an abbreviated text and a misspelled brand.
            *[(15, store, "HUEVO CHOC", "KINDE", "20.00", "GR") for store in range(1, 6)],
            (10, 1, "HUEVO CHOCOLATE SORPRESA KINDER X 20 GRS", "KINDER", "1", "UNI"),
            (11, 1, "KINDER HVO 20G", "KINDER", "0.02", "KG"),
            (12, 1, "HUEVO DE CHOCOLATE KINDER 20 GRM", "Kinder", "20", "grm"),
        ],
    )

    assert load_dim_produto_from_staging(pg_conn, run_id) == 1

    assert _product(pg_conn) == (
        # Every text has one chain: the longest, least abbreviated one wins.
        "HUEVO CHOCOLATE SORPRESA KINDER X 20 GRS",
        # KINDER in any case has three chains against one.
        "KINDER",
        # A measured size beats chain 10's unit count; three spellings of 20 g
        # count as one size, written as its most frequent spelling.
        "20.00",
        "GR",
        Decimal("20"),
        "g",
        4,
    )
    assert _conflicts(pg_conn) == [
        ("marca", [{"valor": "KINDER", "cadenas": 3}, {"valor": "KINDE", "cadenas": 1}]),
    ]


def test_chains_that_disagree_on_measured_size_are_recorded(pg_conn, run_id):
    _stage(
        pg_conn,
        run_id,
        [
            (1, 1, "LECHE ENTERA 1L", "LA SERENISIMA", "1", "LT"),
            (2, 1, "LECHE ENTERA 1L", "LA SERENISIMA", "1000", "ML"),
            (3, 1, "LECHE ENTERA 1L", "LA SERENISIMA", "1", "ML"),
        ],
    )

    load_dim_produto_from_staging(pg_conn, run_id)

    assert _product(pg_conn)[4:] == (Decimal("1000"), "ml", 3)
    assert _conflicts(pg_conn) == [
        (
            "contenido",
            [{"valor": "1000 ml", "cadenas": 2}, {"valor": "1 ml", "cadenas": 1}],
        ),
    ]


def test_a_later_full_load_replaces_the_conflicts_of_its_products(pg_conn, run_id):
    _stage(
        pg_conn,
        run_id,
        [
            (1, 1, "LECHE", "SERENISIMA", "1", "LT"),
            (2, 1, "LECHE", "LA SERENISIMA", "1", "LT"),
        ],
    )
    load_dim_produto_from_staging(pg_conn, run_id)
    assert [attribute for attribute, _ in _conflicts(pg_conn)] == ["marca"]

    with pg_conn.cursor() as cur:
        cur.execute(
            "UPDATE stg_productos SET productos_marca = 'LA SERENISIMA' WHERE run_id = %s",
            (run_id,),
        )
    load_dim_produto_from_staging(pg_conn, run_id)

    assert _conflicts(pg_conn) == []
    assert _product(pg_conn)[1] == "LA SERENISIMA"


def test_a_partial_load_neither_updates_products_nor_records_conflicts(pg_conn, run_id):
    _stage(
        pg_conn,
        run_id,
        [
            (1, 1, "LECHE", "SERENISIMA", "1", "LT"),
            (2, 1, "LECHE", "LA SERENISIMA", "1", "LT"),
        ],
    )

    assert load_dim_produto_from_staging(pg_conn, run_id, update_existing=False) == 1
    assert _conflicts(pg_conn) == []

    with pg_conn.cursor() as cur:
        cur.execute(
            "UPDATE stg_productos SET productos_descripcion = 'OTRA' WHERE run_id = %s",
            (run_id,),
        )
    assert load_dim_produto_from_staging(pg_conn, run_id, update_existing=False) == 0
    assert _product(pg_conn)[0] == "LECHE"


def test_unknown_units_stay_empty(pg_conn, run_id):
    _stage(pg_conn, run_id, [(1, 1, "SOGA", "X", "2", "MT")])

    load_dim_produto_from_staging(pg_conn, run_id)

    assert _product(pg_conn)[2:] == ("2", "MT", None, None, 1)
