"""Catalog search ranks products for reading and names them by their attributes."""

import asyncio
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import pytest

from api.location import Location
from api.routes import products
from etl.load.warehouse_loader import _ensure_partition

FECHA_ID = 20260317

TYPES = [
    ("LECHE", "leche"),
    ("DULCE DE LECHE", "dulce de leche"),
    ("GALLETITAS", "galletitas"),
    ("TE", "té"),
]

# identity: (description, brand, type, variant, size, unit, chains, published)
PRODUCTS = {
    "ean:7790000000011": (
        "LECHE ENT LA SERENISIMA 1L", "LA SERENISIMA", "leche", "entera", 1000, "ml", 2, True
    ),
    "ean:7790000000028": (
        "DULCE DE LECHE LA SERENISIMA 400G", "LA SERENISIMA", "dulce de leche", None, 400, "g",
        5, True,
    ),
    "ean:7790000000035": (
        "GALLETITAS CON LECHE MANA", "MANA", "galletitas", None, 145, "g", 6, True
    ),
    "ean:7790000000042": (
        "LECHE DESCREMADA ILOLAY 1L", "ILOLAY", "leche", "descremada", 1000, "ml", 9, False
    ),
    "ean:7790000000059": ("TE GREEN HILLS X 50", "GREEN HILLS", "té", None, 50, "u", 3, True),
    "12:1:2045": ("PROD SIN TIPO X", "COTO", None, None, None, None, 1, True),
}
LECHE = "ean:7790000000011"

# A Coto at the Obelisco and a Dia about 33 km south of it, in the province.
OBELISCO = (-34.6037, -58.3816)
# store: (chain id, chain, province, locality, location)
STORES = {
    1: (12, "Coto", "CABA", "San Nicolas", OBELISCO),
    2: (15, "Dia", "Buenos Aires", "Lomas de Zamora", (-34.9, -58.3816)),
}


@dataclass
class Catalog:
    conn: object
    run_id: int
    products: dict[str, int]
    stores: dict[int, int]

    def add_product(self, identity, desc, brand, tipo, variant, size, unit, chains, *_):
        """Add a product as ``PRODUCTS`` describes it, without a price."""
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO dim_producto (
                    producto_identity, id_producto, es_ean, productos_descripcion,
                    productos_marca, tipo, variante, contenido, contenido_unidad,
                    cadenas_fuente
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING producto_sk
                """,
                (
                    identity, identity.rsplit(":", 1)[1], identity.startswith("ean:"),
                    desc, brand, tipo, variant, size, unit, chains,
                ),
            )
            self.products[identity] = cur.fetchone()[0]

    def price(self, identity, store, lista, observada=FECHA_ID, promos=()):
        """Publish a price; ``promos`` holds up to two (price, legend) pairs."""
        (promo1, leyenda1), (promo2, leyenda2) = [*promos, *[(None, None)] * 2][:2]
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO fact_precios (
                    producto_sk, sucursal_sk, fecha_id, run_id, precio_lista,
                    precio_promo1, leyenda_promo1, precio_promo2, leyenda_promo2,
                    tiene_promo, fecha_observada_id
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    self.products[identity], self.stores[store], FECHA_ID, self.run_id, lista,
                    promo1, leyenda1, promo2, leyenda2, bool(promos), observada,
                ),
            )


@pytest.fixture()
def catalog(pg_conn, monkeypatch):
    """A published day with the products above, each at 100 in store 1."""
    value = date(2026, 3, 17)
    with pg_conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO producto_tipo_terminos (termino, tipo) VALUES (%s, %s)", TYPES
        )
        cur.execute(
            """
            INSERT INTO dim_fecha (
                fecha_id, fecha, anio, mes, trimestre, semana_iso, dia_semana, es_fin_semana
            ) VALUES (%s, %s, 2026, 3, 1, 12, 2, FALSE)
            """,
            (FECHA_ID, value),
        )
        cur.execute(
            """
            INSERT INTO raw_files (filename, url, sha256, size_bytes, fecha_datos)
            VALUES ('published.zip', 'https://example.test/p.zip', 'search', 1, %s)
            RETURNING id
            """,
            (value,),
        )
        raw_id = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO ingestion_runs (raw_file_id, status, rows_fact_published, finished_at)
            VALUES (%s, 'completed', 1, NOW()) RETURNING id
            """,
            (raw_id,),
        )
        run_id = cur.fetchone()[0]
        stores = {}
        for store, (chain_id, chain, province, locality, (lat, lon)) in STORES.items():
            cur.execute(
                """
                INSERT INTO dim_comercio (id_comercio, id_bandera, comercio_bandera_nombre)
                VALUES (%s, 1, %s) RETURNING comercio_sk
                """,
                (chain_id, chain),
            )
            comercio_sk = cur.fetchone()[0]
            cur.execute(
                """
                INSERT INTO dim_provincia (codigo_iso, nombre, region)
                VALUES (%s, %s, 'AMBA') RETURNING provincia_sk
                """,
                (f"AR-{store}", province),
            )
            provincia_sk = cur.fetchone()[0]
            cur.execute(
                """
                INSERT INTO dim_sucursal (
                    id_comercio, id_bandera, id_sucursal, comercio_sk, provincia_sk,
                    sucursales_localidad, sucursales_latitud, sucursales_longitud
                ) VALUES (%s, 1, 1, %s, %s, %s, %s, %s) RETURNING sucursal_sk
                """,
                (chain_id, comercio_sk, provincia_sk, locality, lat, lon),
            )
            stores[store] = cur.fetchone()[0]
    _ensure_partition(pg_conn, FECHA_ID)
    catalog = Catalog(pg_conn, run_id, {}, stores)
    for identity, row in PRODUCTS.items():
        catalog.add_product(identity, *row)
        if row[-1]:
            catalog.price(identity, 1, 100)

    async def fetch(sql: str, *params):
        # asyncpg infers each parameter's type from the statement.
        assert {int(n) for n in re.findall(r"\$(\d+)", sql)} == set(range(1, len(params) + 1))
        with pg_conn.cursor() as cur:
            # asyncpg reuses $1; name the placeholders so psycopg can too.
            cur.execute(
                re.sub(r"\$(\d+)", r"%(p\1)s", sql),
                {f"p{i}": value for i, value in enumerate(params, start=1)},
            )
            columns = [column.name for column in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]

    monkeypatch.setattr(products, "fetch", fetch)
    return catalog


def _facets(q, **filters):
    filters = {"tipo": None, "marca": None, "contenido": None, "unidad": None, **filters}
    return asyncio.run(products.search_facets(q, **filters))


def _search(q, **filters):
    filters = {
        "tipo": None, "marca": None, "contenido": None, "unidad": None,
        "provincia": None, "localidad": None, "cadenas": None,
        "location": None, "limit": 50, **filters,
    }
    return asyncio.run(products.search_products(q, **filters))


def _names(q, **filters):
    return [row["nombre"] for row in _search(q, **filters)]


@pytest.mark.parametrize(
    "contenido, unidad, texto",
    [
        (Decimal("500"), "g", "500 g"),
        (Decimal("41.5"), "g", "41,5 g"),
        (Decimal("1000"), "g", "1 kg"),
        (Decimal("1500"), "ml", "1,5 l"),
        (Decimal("6"), "u", "x 6"),
        (Decimal("1"), "u", None),
        (None, None, None),
    ],
)
def test_a_size_is_written_for_reading(pg_conn, contenido, unidad, texto):
    with pg_conn.cursor() as cur:
        cur.execute("SELECT pipeline_format_size(%s, %s)", (contenido, unidad))
        assert cur.fetchone()[0] == texto


def test_the_name_comes_from_the_attributes(catalog):
    [leche] = _search("7790000000011")

    assert leche["nombre"] == "Leche La Serenisima entera 1 l"
    assert leche["descripcion"] == "LECHE ENT LA SERENISIMA 1L"
    assert (leche["tipo"], leche["variante"]) == ("leche", "entera")


def test_a_product_without_a_type_keeps_its_description(catalog):
    assert _names("prod sin tipo") == ["PROD SIN TIPO X"]


def test_the_type_the_query_names_comes_first(catalog):
    # The dulce de leche and the cookies are sold by more chains.
    assert _names("leche") == [
        "Leche La Serenisima entera 1 l",
        "Galletitas Mana 145 g",
        "Dulce de leche La Serenisima 400 g",
    ]


def test_only_products_with_a_published_price_are_found(catalog):
    assert "Leche Ilolay descremada 1 l" not in _names("leche")
    assert _search("7790000000042") == []


def test_every_word_must_match_in_any_order_case_or_accent(catalog):
    # The type named after the brand still outranks the more listed product.
    assert _names("SERENÍSIMA léche") == [
        "Leche La Serenisima entera 1 l",
        "Dulce de leche La Serenisima 400 g",
    ]


def test_an_exact_ean_comes_first(catalog):
    assert [row["id_producto"] for row in _search("7790000000028")] == ["7790000000028"]


def test_a_scanned_code_is_found_without_its_leading_zeros(catalog):
    catalog.add_product("ean:0000077900124", "PILAS X 2", "DURACELL", None, None, 2, "u", 1)
    catalog.price("ean:0000077900124", 1, 10)

    assert [row["id_producto"] for row in _search("7790-0124")] == ["0000077900124"]


def test_a_query_of_short_words_finds_the_type_it_names(catalog):
    assert _names("té") == ["Té Green Hills x 50"]
    assert _search("de") == []


@pytest.mark.parametrize(
    "filters, names",
    [
        ({"tipo": "dulce de leche"}, ["Dulce de leche La Serenisima 400 g"]),
        (
            {"marca": "la serenísima"},
            ["Leche La Serenisima entera 1 l", "Dulce de leche La Serenisima 400 g"],
        ),
        ({"contenido": Decimal("1000"), "unidad": "ml"}, ["Leche La Serenisima entera 1 l"]),
    ],
)
def test_filters_narrow_the_results(catalog, filters, names):
    assert _names("leche", **filters) == names


def test_search_reads_the_corrected_attributes(pg_conn, catalog):
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO producto_overrides (producto_identity, marca, variante, nota)
            VALUES ('ean:7790000000035', 'TERRABUSI', 'vainilla', 'Chain descriptions')
            """
        )

    # Neither word is in the voted text the index covers.
    [found] = _search("terrabusi vainilla")

    assert found["nombre"] == "Galletitas Terrabusi vainilla 145 g"
    assert found["marca"] == "TERRABUSI"


def _offer(q, **filters):
    [row] = [row for row in _search(q, **filters) if row["nombre"].startswith("Leche")]
    return row["precio_desde"], row["fecha_precio_desde"], row["sucursales"]


def test_a_product_is_offered_from_its_lowest_store_price(catalog):
    # A conditional promotion (3x2) does not set the store price; the
    # unconditional one does, and its date is the day it was observed.
    catalog.price(
        LECHE, 2, 120, observada=20260316,
        promos=[(Decimal("80"), "3X2 JUGOS"), (Decimal("95"), "20% de descuento")],
    )

    assert _offer("leche") == (Decimal("95"), "2026-03-16", 2)


def test_the_from_price_is_also_given_per_kg_or_l(catalog):
    by_name = {row["nombre"]: row["precio_unitario_desde"] for row in _search("la serenisima")}

    assert by_name == {
        "Leche La Serenisima entera 1 l": {"precio": Decimal("100.00"), "unidad": "l"},
        "Dulce de leche La Serenisima 400 g": {"precio": Decimal("250.00"), "unidad": "kg"},
    }
    # Sold by the unit: no price per kg or l.
    assert [row["precio_unitario_desde"] for row in _search("green hills")] == [None]


def test_a_store_with_a_promotion_above_its_list_price_offers_the_list_price(catalog):
    catalog.price(LECHE, 2, 90, promos=[(Decimal("130"), "20% de descuento")])

    assert _offer("leche") == (Decimal("90"), "2026-03-17", 2)


def test_only_nearby_stores_count_with_a_location(catalog):
    catalog.price(LECHE, 2, 90)
    lat, lon = OBELISCO

    assert _offer("leche") == (Decimal("90"), "2026-03-17", 2)
    assert _offer("leche", location=Location(lat, lon, 5)) == (Decimal("100"), "2026-03-17", 1)


def test_a_product_carried_by_no_nearby_store_has_no_from_price(catalog):
    lat, lon = STORES[2][-1]

    # Still listed, in the same order.
    assert _names("leche", location=Location(lat, lon, 1)) == _names("leche")
    assert _offer("leche", location=Location(lat, lon, 1)) == (None, None, 0)


@pytest.mark.parametrize(
    "stores, offer",
    [
        ({"cadenas": ["Dia"]}, (Decimal("90"), "2026-03-17", 1)),
        ({"cadenas": ["Coto", "Dia"]}, (Decimal("90"), "2026-03-17", 2)),
        ({"provincia": "CABA"}, (Decimal("100"), "2026-03-17", 1)),
        ({"provincia": "Buenos Aires", "localidad": "Lomas de Zamora"},
         (Decimal("90"), "2026-03-17", 1)),
        ({"localidad": "Palermo"}, (None, None, 0)),
    ],
)
def test_store_filters_choose_the_stores_that_count(catalog, stores, offer):
    catalog.price(LECHE, 2, 90)

    assert _offer("leche", **stores) == offer
    assert _names("leche", **stores) == _names("leche")


def _values(facet):
    return [(value["valor"], value["productos"]) for value in facet]


def test_facets_count_the_products_a_search_lists(catalog):
    # Ilolay has no published price, so it is not counted.
    facets = _facets("leche")

    assert _values(facets["tipos"]) == [
        ("dulce de leche", 1), ("galletitas", 1), ("leche", 1)
    ]
    assert _values(facets["marcas"]) == [("La Serenisima", 2), ("Mana", 1)]
    assert facets["tamanos"] == [
        {"valor": "1 l", "productos": 1, "contenido": Decimal("1000"), "unidad": "ml"},
        {"valor": "145 g", "productos": 1, "contenido": Decimal("145"), "unidad": "g"},
        {"valor": "400 g", "productos": 1, "contenido": Decimal("400"), "unidad": "g"},
    ]


def test_brands_are_grouped_without_regard_to_case_or_accents(catalog):
    for n, brand in enumerate(["La Serenísima", "LA SERENISIMA"]):
        identity = f"ean:779000000010{n}"
        catalog.add_product(identity, "LECHE ENT 500ML", brand, "leche", None, 500, "ml", 1)
        catalog.price(identity, 1, 50)

    # Three products write it in capitals, one with an accent.
    assert _values(_facets("leche")["marcas"]) == [("La Serenisima", 4), ("Mana", 1)]
    assert len(_search("leche", marca="la serenisima")) == 4


def test_a_facet_ignores_its_own_filter_and_applies_the_others(catalog):
    facets = _facets("leche", tipo="leche", marca="mana")

    # Every type of the brand, every brand of the type.
    assert _values(facets["tipos"]) == [("galletitas", 1)]
    assert _values(facets["marcas"]) == [("La Serenisima", 1)]
    assert facets["tamanos"] == []


def test_a_blank_brand_and_a_single_unit_are_not_choices(catalog):
    catalog.add_product("ean:7790000000110", "TE X 1", " ", "té", None, 1, "u", 1)
    catalog.price("ean:7790000000110", 1, 10)

    facets = _facets("te")

    assert _values(facets["marcas"]) == [("Green Hills", 1)]
    assert _values(facets["tamanos"]) == [("x 50", 1)]


def _product(producto_sk):
    return asyncio.run(products.get_product(producto_sk))


def test_detail_shows_the_published_description_next_to_its_correction(catalog):
    with catalog.conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO producto_overrides (producto_identity, descripcion, nota)
            VALUES (%s, 'LECHE ENTERA LA SERENISIMA 1L', 'abbreviation')
            """,
            (LECHE,),
        )

    product = _product(catalog.products[LECHE])

    assert product["nombre"] == "Leche La Serenisima entera 1 l"
    assert product["descripcion"] == "LECHE ENTERA LA SERENISIMA 1L"
    assert product["corregido"] is True
    assert product["publicado"]["descripcion"] == "LECHE ENT LA SERENISIMA 1L"
    assert product["codigos"] == {
        "identidad": LECHE, "id_producto": "7790000000011", "es_ean": True,
        "id_comercio": None, "id_bandera": None, "cadena": None,
    }


def test_detail_names_the_chain_that_scopes_a_product_without_ean(catalog):
    with catalog.conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO dim_producto (
                producto_identity, id_producto, es_ean, productos_descripcion,
                productos_marca, productos_cantidad_presentacion,
                productos_unidad_medida_presentacion, source_id_comercio, source_id_bandera
            ) VALUES ('chain:12:1:2300049', '2300049', FALSE, 'QUESO CREMOSO X KG',
                      'COTO', '1', 'KG', 12, 1)
            RETURNING producto_sk
            """
        )
        producto_sk = cur.fetchone()[0]

    product = _product(producto_sk)

    assert product["corregido"] is False
    assert product["publicado"] == {
        "descripcion": "QUESO CREMOSO X KG", "marca": "COTO", "cantidad": "1", "unidad": "KG",
    }
    assert product["codigos"] == {
        "identidad": "chain:12:1:2300049", "id_producto": "2300049", "es_ean": False,
        "id_comercio": 12, "id_bandera": 1, "cadena": "Coto",
    }


def test_coverage_explains_a_chain_code_and_few_stores(catalog):
    # The catalog prices each product in one store.
    assert {row["nombre"]: row["cobertura"] for row in _search("leche")} == {
        name: ["pocos_locales"] for name in _names("leche")
    }
    # A product no chosen store carries is not "few stores".
    far = _search("leche", location=Location(*STORES[2][4], 1))
    assert {tuple(row["cobertura"]) for row in far} == {()}
    assert _product(catalog.products["12:1:2045"])["cobertura"] == ["codigo_interno"]
    assert _product(catalog.products[LECHE])["cobertura"] == []


def test_detail_of_an_unknown_product_is_not_found(catalog):
    with pytest.raises(products.HTTPException) as error:
        _product(-1)

    assert error.value.status_code == 404
