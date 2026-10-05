"""A product's type comes from a curated term dictionary and a chain vote."""

from pathlib import Path

import psycopg
import pytest

from etl.load.warehouse_loader import load_dim_produto_from_staging

EAN = "7791234567898"
SEED = Path(__file__).parent.parent.parent / "db" / "seeds" / "seed_producto_tipos.sql"

TERMS = [
    ("YOG", "yogur", True),
    ("YOGUR", "yogur", True),
    ("LECHE", "leche", False),
    ("DULCE DE LECHE", "dulce de leche", False),
    ("ALFAJOR", "alfajor", False),
    ("SH", "shampoo", True),
    ("ALIM", "alimento para mascotas", False),
    ("PANAL", "pañales", False),
]


@pytest.fixture()
def terms(pg_conn):
    with pg_conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO producto_tipo_terminos (termino, tipo, tras_marca) VALUES (%s, %s, %s)",
            TERMS,
        )


@pytest.fixture()
def run_id(pg_conn) -> int:
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO raw_files (filename, url, sha256, size_bytes, fecha_datos)
            VALUES ('test.zip', 'http://test', 'product-type', 1, '2026-03-24')
            RETURNING id
            """
        )
        raw_file_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO ingestion_runs (raw_file_id, status) VALUES (%s, 'running') RETURNING id",
            (raw_file_id,),
        )
        return cur.fetchone()[0]


def _classify(pg_conn, descripcion):
    with pg_conn.cursor() as cur:
        cur.execute("SELECT pipeline_product_type(%s)", (descripcion,))
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


def _type(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT tipo FROM dim_producto WHERE producto_identity = %s", (f"ean:{EAN}",)
        )
        return cur.fetchone()[0]


@pytest.mark.parametrize(
    "descripcion, tipo",
    [
        pytest.param("YOG BEBIBLE FRUTILLA 900G", "yogur", id="abbreviation"),
        pytest.param("Yogur entero", "yogur", id="any case"),
        pytest.param("DULCE DE LECHE CLASICO 400G", "dulce de leche", id="longest phrase"),
        pytest.param("DULCE D/LECHE COLONIAL", None, id="phrase spelled otherwise"),
        pytest.param("ALFAJOR TRIPLE DULCE DE LECHE", "alfajor", id="earliest term"),
        pytest.param("ALIM.P/GATOS ADULTOS", "alimento para mascotas", id="punctuation"),
        pytest.param("PAÑAL PAMPERS XG", "pañales", id="enye as n"),
        pytest.param("SEDAL SH CERAMIDAS", "shampoo", id="behind a brand"),
        pytest.param("ILOLAY CREMOSO YOG FIRME", "yogur", id="third word"),
        pytest.param("ILOLAY CREMOSO FIRME YOG", None, id="fourth word"),
        pytest.param("HUEVO CHOCOLATE LECHE", None, id="flavor behind another word"),
        pytest.param("CINDOR 200ML", None, id="unknown"),
        pytest.param("", None, id="empty"),
    ],
)
def test_a_description_is_classified_by_its_leading_term(pg_conn, terms, descripcion, tipo):
    assert _classify(pg_conn, descripcion) == tipo


def test_the_chains_vote_on_the_type(pg_conn, terms, run_id):
    _stage(
        pg_conn,
        run_id,
        [
            # Chain 1 has many stores; it still votes once.
            *[(1, store, "LECHE CULTIVADA FRUTILLA") for store in range(1, 6)],
            (2, 1, "YOG BEBIBLE FRUTILLA"),
            (3, 1, "LA SERENISIMA YOGUR FRUTILLA"),
            # An unclassified description does not vote.
            (4, 1, "BEBIBLE FRUTILLA 900G"),
        ],
    )

    load_dim_produto_from_staging(pg_conn, run_id)

    assert _type(pg_conn) == "yogur"


def test_an_unclassified_product_keeps_an_empty_type(pg_conn, terms, run_id):
    _stage(pg_conn, run_id, [(1, 1, "CINDOR 200ML")])

    load_dim_produto_from_staging(pg_conn, run_id)

    assert _type(pg_conn) is None


def test_a_dictionary_change_reaches_the_next_full_load(pg_conn, terms, run_id):
    _stage(pg_conn, run_id, [(1, 1, "CINDOR 200ML")])
    load_dim_produto_from_staging(pg_conn, run_id)

    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO producto_tipo_terminos (termino, tipo) VALUES ('CINDOR', 'leche chocolatada')"
        )
    load_dim_produto_from_staging(pg_conn, run_id, update_existing=False)
    assert _type(pg_conn) is None

    load_dim_produto_from_staging(pg_conn, run_id)
    assert _type(pg_conn) == "leche chocolatada"


@pytest.mark.parametrize(
    "termino, tipo",
    [
        pytest.param("yog", "yogur", id="lower case term"),
        pytest.param("CAFÉ", "café", id="accented term"),
        pytest.param("P.HIG", "papel higiénico", id="punctuation in term"),
        pytest.param("DULCE DE LECHE REPOSTERO", "dulce de leche", id="four words"),
        pytest.param("YOG", "Yogur", id="upper case type"),
        pytest.param("YOG", " ", id="blank type"),
    ],
)
def test_invalid_terms_are_rejected(pg_conn, termino, tipo):
    with pytest.raises(psycopg.errors.CheckViolation):
        with pg_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO producto_tipo_terminos (termino, tipo) VALUES (%s, %s)",
                (termino, tipo),
            )


def test_an_override_corrects_the_type(pg_conn, terms, run_id):
    _stage(pg_conn, run_id, [(1, 1, "CINDOR 200ML")])
    load_dim_produto_from_staging(pg_conn, run_id)

    with pg_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO producto_overrides (producto_identity, tipo, nota)
            VALUES (%s, 'leche chocolatada', 'Brand-only description')
            """,
            (f"ean:{EAN}",),
        )
        cur.execute(
            "SELECT tipo, corregido FROM v_producto WHERE producto_identity = %s",
            (f"ean:{EAN}",),
        )
        assert cur.fetchone() == ("leche chocolatada", True)


@pytest.mark.parametrize(
    "descripcion, tipo",
    [
        ("SH SEDAL CERAMIDAS X 340 ML", "shampoo"),
        ("TRESEMME SH KER ANTF", "shampoo"),
        ("ACO PANTENE KERATINA X 250 ML", "acondicionador"),
        ("REXONA M CLINICAL DEO", None),
        ("ALIM.P/GATOS ADULTOS CARNE Y POLLO CAT CHOW BSA 8 KGM", "alimento para mascotas"),
        ("DULCE DE LECHE CLASICO LA SERENISIMA X 400 GRS", "dulce de leche"),
        ("ALFAJOR TRIPLE DULCE DE LECHE", "alfajor"),
        ("P.HIGIENICO S/H FELPITA PAQ 12", "papel higiénico"),
        ("CREMA DENTAL COLGATE TOTAL 90 G", "pasta dental"),
        ("CREMA DE LECHE TREGAR POT 350 GRM", "crema de leche"),
        ("GALL CHOC RELL 100G", "galletitas"),
        ("PISTOLA DE AGUA", None),
        ("JUEGO DE TE", None),
        ("TAB MILKA OREO", None),
        ("PANUELOS DESCARTABLES ELITE X 6", "pañuelos descartables"),
        ("BIZCOCHOS DE ARROZ GALLO 100G", "galletas de arroz"),
        ("TOALLAS FEM DONCELLA X 8", "toallas femeninas"),
        ("JABON LIQ ROPA ALA 800CC", "jabón para ropa"),
        ("LECHE CHOCOLATADA CINDOR 1L", "leche chocolatada"),
    ],
)
def test_the_seed_classifies_real_descriptions(pg_conn, descripcion, tipo):
    seed = SEED.read_text().replace("BEGIN;", "").replace("COMMIT;", "")
    with pg_conn.cursor() as cur:
        cur.execute(seed)

    assert _classify(pg_conn, descripcion) == tipo
