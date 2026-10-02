"""Catalog search ranks products for reading and names them by their attributes."""

import asyncio
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import pytest

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

# A store at the Obelisco and another about 33 km south of it.
OBELISCO = (-34.6037, -58.3816)
STORES = {1: OBELISCO, 2: (-34.9, -58.3816)}


@dataclass
class Catalog:
    conn: object
    run_id: int
    products: dict[str, int]
    stores: dict[int, int]

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
        cur.execute(
            """
            INSERT INTO dim_comercio (id_comercio, id_bandera, comercio_bandera_nombre)
            VALUES (12, 1, 'Coto') RETURNING comercio_sk
            """
        )
        comercio_sk = cur.fetchone()[0]
        stores = {}
        for store, (lat, lon) in STORES.items():
            cur.execute(
                """
                INSERT INTO dim_sucursal (
                    id_comercio, id_bandera, id_sucursal, comercio_sk,
                    sucursales_latitud, sucursales_longitud
                ) VALUES (12, 1, %s, %s, %s, %s) RETURNING sucursal_sk
                """,
                (store, comercio_sk, lat, lon),
            )
            stores[store] = cur.fetchone()[0]
        keys = {}
        for identity, (desc, brand, tipo, variant, size, unit, chains, _) in PRODUCTS.items():
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
            keys[identity] = cur.fetchone()[0]
    _ensure_partition(pg_conn, FECHA_ID)
    catalog = Catalog(pg_conn, run_id, keys, stores)
    for identity, row in PRODUCTS.items():
        if row[-1]:
            catalog.price(identity, 1, 100)

    async def fetch(sql: str, *params):
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


def _search(q, **filters):
    filters = {
        "tipo": None, "marca": None, "contenido": None, "unidad": None,
        "lat": None, "lon": None, "radius": 1, "limit": 50, **filters,
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


def test_a_store_with_a_promotion_above_its_list_price_offers_the_list_price(catalog):
    catalog.price(LECHE, 2, 90, promos=[(Decimal("130"), "20% de descuento")])

    assert _offer("leche") == (Decimal("90"), "2026-03-17", 2)


def test_only_nearby_stores_count_with_a_location(catalog):
    catalog.price(LECHE, 2, 90)
    lat, lon = OBELISCO

    assert _offer("leche") == (Decimal("90"), "2026-03-17", 2)
    assert _offer("leche", lat=lat, lon=lon, radius=5) == (Decimal("100"), "2026-03-17", 1)


def test_a_product_carried_by_no_nearby_store_has_no_from_price(catalog):
    lat, lon = STORES[2]

    # Still listed, in the same order.
    assert _names("leche", lat=lat, lon=lon) == _names("leche")
    assert _offer("leche", lat=lat, lon=lon) == (None, None, 0)
