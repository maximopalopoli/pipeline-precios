"""A product's variant comes from a curated term dictionary and a chain vote."""

from pathlib import Path

import psycopg
import pytest

from etl.load.warehouse_loader import load_dim_produto_from_staging

EAN = "7791234567898"
SEEDS = Path(__file__).parent.parent.parent / "db" / "seeds"

TYPES = [
    ("YOG", "yogur", True),
    ("AGUA", "agua", False),
    ("DULCE DE LECHE", "dulce de leche", False),
    ("ALFAJOR", "alfajor", False),
]

VARIANTS = [
    ("VAI", "vainilla"),
    ("VAINILLA", "vainilla"),
    ("FIRME", "firme"),
    ("S G", "sin gas"),
    ("S GAS", "sin gas"),
    ("SIN GAS", "sin gas"),
    ("LIMON", "limón"),
    ("LIMA LIMON", "lima limón"),
    ("POMELO", "pomelo"),
    ("POMELO ROSADO", "pomelo rosado"),
    ("ROSADO", "rosado"),
    ("DULCE DE LECHE", "dulce de leche"),
    ("DDL", "dulce de leche"),
    ("CERO", "sin azúcar"),
]


@pytest.fixture()
def terms(pg_conn):
    with pg_conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO producto_tipo_terminos (termino, tipo, tras_marca) VALUES (%s, %s, %s)",
            TYPES,
        )
        cur.executemany(
            "INSERT INTO producto_variante_terminos (termino, variante) VALUES (%s, %s)",
            VARIANTS,
        )


@pytest.fixture()
def run_id(pg_conn) -> int:
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO raw_files (filename, url, sha256, size_bytes, fecha_datos)
            VALUES ('test.zip', 'http://test', 'product-variant', 1, '2026-03-24')
            RETURNING id
            """
        )
        raw_file_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO ingestion_runs (raw_file_id, status) VALUES (%s, 'running') RETURNING id",
            (raw_file_id,),
        )
        return cur.fetchone()[0]


def _variant_of(pg_conn, descripcion, tipo=None):
    with pg_conn.cursor() as cur:
        cur.execute("SELECT pipeline_product_variant(%s, %s)", (descripcion, tipo))
        return cur.fetchone()[0]


def _stage(pg_conn, run_id, rows):
    """Stage rows of (chain, store, description)."""
    with pg_conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO stg_productos (
                run_id, id_comercio, id_bandera, id_sucursal, id_producto,
                productos_ean, productos_descripcion, productos_marca,
                productos_cantidad_presentacion, productos_unidad_medida_presentacion,
                productos_cantidad_referencia, productos_unidad_medida_referencia
            ) VALUES (%s, %s, '1', %s, %s, '1', %s, 'X', '1', 'UN', '1', 'UN')
            """,
            [(run_id, chain, store, EAN, descripcion) for chain, store, descripcion in rows],
        )


def _loaded_variant(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT variante FROM dim_producto WHERE producto_identity = %s", (f"ean:{EAN}",)
        )
        return cur.fetchone()[0]


@pytest.mark.parametrize(
    "descripcion, tipo, variante",
    [
        pytest.param("YOG FIRME VAI 190G", "yogur", "firme, vainilla", id="sorted values"),
        pytest.param("Yogur firme vainilla", "yogur", "firme, vainilla", id="any case"),
        pytest.param("AGUA S/GAS LIMON", "agua", "limón, sin gas", id="punctuation"),
        pytest.param("AGUA LIMA LIMON", "agua", "lima limón", id="longer term contains"),
        pytest.param("AGUA POMELO ROSADO", "agua", "pomelo rosado", id="longer term ends"),
        pytest.param("AGUA VAI VAINILLA", "agua", "vainilla", id="value named twice"),
        pytest.param("ALFAJOR DDL", "alfajor", "dulce de leche", id="flavor of a type"),
        pytest.param("DULCE DE LECHE CERO", "dulce de leche", "sin azúcar", id="own type left out"),
        pytest.param("DULCE DE LECHE 400G", "dulce de leche", None, id="only its own type"),
        pytest.param("ILOLAY YOG 190G", "yogur", None, id="unknown"),
        pytest.param("", None, None, id="empty"),
    ],
)
def test_a_description_names_its_variant(pg_conn, terms, descripcion, tipo, variante):
    assert _variant_of(pg_conn, descripcion, tipo) == variante


def test_the_chains_vote_on_the_variant(pg_conn, terms, run_id):
    _stage(
        pg_conn,
        run_id,
        [
            # Chain 1 has many stores; it still votes once.
            *[(1, store, "YOG VAI") for store in range(1, 6)],
            (2, 1, "YOG FIRME VAINILLA"),
            (3, 1, "ILOLAY YOG FIRME VAI"),
            # A description that names no variant does not vote.
            (4, 1, "ILOLAY YOG 190G"),
            (5, 1, "ILOLAY YOG 190G"),
        ],
    )

    load_dim_produto_from_staging(pg_conn, run_id)

    assert _loaded_variant(pg_conn) == "firme, vainilla"


def test_a_tie_goes_to_the_variant_with_more_values(pg_conn, terms, run_id):
    _stage(pg_conn, run_id, [(1, 1, "AGUA LIMON"), (2, 1, "AGUA S/G LIMON")])

    load_dim_produto_from_staging(pg_conn, run_id)

    assert _loaded_variant(pg_conn) == "limón, sin gas"


def test_the_product_type_is_left_out_of_the_vote(pg_conn, terms, run_id):
    _stage(pg_conn, run_id, [(1, 1, "DULCE DE LECHE 400G"), (2, 1, "DULCE DE LECHE CERO")])

    load_dim_produto_from_staging(pg_conn, run_id)

    assert _loaded_variant(pg_conn) == "sin azúcar"


def test_a_product_that_names_no_variant_keeps_it_empty(pg_conn, terms, run_id):
    _stage(pg_conn, run_id, [(1, 1, "ILOLAY YOG 190G")])

    load_dim_produto_from_staging(pg_conn, run_id)

    assert _loaded_variant(pg_conn) is None


@pytest.mark.parametrize(
    "termino, variante",
    [
        pytest.param("vai", "vainilla", id="lower case term"),
        pytest.param("LIMÓN", "limón", id="accented term"),
        pytest.param("S/G", "sin gas", id="punctuation in term"),
        pytest.param("SIN TACC NI GLUTEN", "sin tacc", id="four words"),
        pytest.param("VAI", "Vainilla", id="upper case variant"),
        pytest.param("VAI", " ", id="blank variant"),
    ],
)
def test_invalid_terms_are_rejected(pg_conn, termino, variante):
    with pytest.raises(psycopg.errors.CheckViolation):
        with pg_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO producto_variante_terminos (termino, variante) VALUES (%s, %s)",
                (termino, variante),
            )


def test_an_override_corrects_the_variant(pg_conn, terms, run_id):
    _stage(pg_conn, run_id, [(1, 1, "ILOLAY YOG 190G")])
    load_dim_produto_from_staging(pg_conn, run_id)

    with pg_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO producto_overrides (producto_identity, variante, nota)
            VALUES (%s, 'frutilla', 'Flavor only on the package')
            """,
            (f"ean:{EAN}",),
        )
        cur.execute(
            "SELECT variante, corregido FROM v_producto WHERE producto_identity = %s",
            (f"ean:{EAN}",),
        )
        assert cur.fetchone() == ("frutilla", True)


def _load_seed(pg_conn, name):
    seed = (SEEDS / name).read_text().replace("BEGIN;", "").replace("COMMIT;", "")
    with pg_conn.cursor() as cur:
        cur.execute(seed)


@pytest.mark.parametrize(
    "descripcion, variante",
    [
        ("VILLA DEL SUR LEVITE S/G MANZANA PET X 500cc", "manzana, sin gas"),
        ("LECHE PARC DESC LA SERENISIMA 1L", "parcialmente descremada"),
        ("YOG FIRME VAI ILOLAY 190G", "firme, vainilla"),
        ("SPRITE LIMA LIMON 2.25L", "lima limón"),
        ("DULCE DE LECHE CLASICO LA SERENISIMA X 400 GRS", None),
        ("ALFAJOR TRIPLE DULCE DE LECHE", "dulce de leche"),
        ("POWERADE FRUTAS TROPICALES PET X 500CC", None),
        ("LECHE DESC SANCOR 1L", None),
        ("LECHE UAT PAR DESCREMADA 1L", "parcialmente descremada"),
        ("SAL ENT/FIN CELUSAL 500G", "entrefina"),
        ("MERMELADA DE CIRUELA ARCOR 454G", "ciruela"),
    ],
)
def test_the_seed_names_variants_of_real_descriptions(pg_conn, descripcion, variante):
    _load_seed(pg_conn, "seed_producto_tipos.sql")
    _load_seed(pg_conn, "seed_producto_variantes.sql")

    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT pipeline_product_variant(%s, pipeline_product_type(%s))",
            (descripcion, descripcion),
        )
        assert cur.fetchone()[0] == variante
