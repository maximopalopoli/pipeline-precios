"""Product search and product detail endpoints."""

import re
import unicodedata
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from api.coverage import coverage_notes
from api.db import fetch
from api.location import Location, optional_location, within_sql
from api.price_date import PRICE_DATE_SQL
from api.pricing import store_priced_facts
from api.published_stores import LATEST_PUBLISHED_FECHA_ID
from api.unit_price import unit_price

router = APIRouter()

# Words shorter than this match almost every product ("DE", "X", the "5" of
# "1.5"); they are left out of the query.
MIN_WORD_LENGTH = 2
MAX_WORDS = 8
# pg_trgm indexes a LIKE pattern only from three letters on.
TRIGRAM_LENGTH = 3
# Facet values listed per attribute, the most common first.
MAX_FACET_VALUES = 20


def query_words(q: str) -> list[str]:
    """The query's words, normalized as pipeline_normalize_text does in SQL."""
    without_accents = "".join(
        c for c in unicodedata.normalize("NFD", q.upper()) if unicodedata.category(c) != "Mn"
    )
    words = re.sub(r"[^A-Z0-9]+", " ", without_accents).split()
    unique = list(dict.fromkeys(w for w in words if len(w) >= MIN_WORD_LENGTH))
    return unique[:MAX_WORDS]


class _Matching:
    """The products a query matches, as SQL over ``v_producto p``.

    Values are added through :meth:`param`, and the query text through
    :meth:`query` only where it is used: asyncpg must infer the type of every
    parameter from the statement.
    """

    def __init__(self, q: str):
        self.q = q
        self.words = query_words(q)
        self.ean = q.strip() if q.strip().isdigit() else None
        self.params: list = []
        self._query_ref: str | None = None
        # The product the query spells as an EAN, if any.
        self.is_ean = f"p.producto_identity = 'ean:' || {self.param(self.ean)}"

        patterns = {word: self.param(f"%{word}%") for word in self.words}
        # Candidates come from indexes: the voted attributes through trigrams
        # (words of at least three letters), the type the query names when it
        # has no such word, and the few products with an override, whose
        # corrected text is checked below.
        type_named = None
        indexed = [pattern for word, pattern in patterns.items() if len(word) >= TRIGRAM_LENGTH]
        self.candidates = [
            "SELECT producto_sk FROM dim_producto p WHERE " + self.is_ean
        ]
        if indexed:
            text = (
                "pipeline_product_search_text("
                "productos_descripcion, productos_marca, tipo, variante)"
            )
            self.candidates.append(
                "SELECT producto_sk FROM dim_producto WHERE "
                + " AND ".join(f"{text} LIKE {pattern}" for pattern in indexed)
            )
        elif self.words:
            self.candidates.append(
                "SELECT producto_sk FROM dim_producto WHERE "
                f"tipo = pipeline_product_type({self.query()})"
            )
            type_named = f"p.tipo = pipeline_product_type({self.query()})"
        if self.words:
            self.candidates.append(
                "SELECT d.producto_sk FROM producto_overrides o "
                "JOIN dim_producto d ON d.producto_identity = o.producto_identity"
            )

        self.conditions = [
            f"""EXISTS (
                SELECT 1 FROM fact_precios f
                WHERE f.producto_sk = p.producto_sk
                  AND f.fecha_id = {LATEST_PUBLISHED_FECHA_ID}
            )"""
        ]
        if self.words:
            matches = " AND ".join(
                f"p.texto_busqueda LIKE {pattern}" for pattern in patterns.values()
            )
            if type_named:
                matches = f"{type_named} AND {matches}"
            self.conditions.append(f"({self.is_ean} OR ({matches}))")

    @property
    def empty(self) -> bool:
        return not self.words and self.ean is None

    def param(self, value) -> str:
        self.params.append(value)
        return f"${len(self.params)}"

    def query(self) -> str:
        """The parameter holding the query text."""
        if self._query_ref is None:
            self._query_ref = self.param(self.q)
        return self._query_ref

    def attribute_filters(self, tipo, marca, contenido, unidad) -> dict[str, str]:
        """Conditions of the attribute filters given, keyed by facet."""
        filters = {}
        if tipo:
            filters["tipo"] = f"p.tipo = {self.param(tipo)}"
        if marca:
            filters["marca"] = (
                "pipeline_normalize_text(p.productos_marca) = "
                f"pipeline_normalize_text({self.param(marca)})"
            )
        sizes = []
        if contenido is not None:
            sizes.append(f"p.contenido = {self.param(contenido)}")
        if unidad:
            sizes.append(f"p.contenido_unidad = {self.param(unidad)}")
        if sizes:
            filters["tamano"] = " AND ".join(sizes)
        return filters

    def candidates_cte(self) -> str:
        # Materialized so that the text checks run on the candidates only;
        # inlined, the planner applied them to every product (about 700 ms).
        return f"""candidatos AS MATERIALIZED (
            SELECT p.*
            FROM v_producto p
            WHERE p.producto_sk IN ({" UNION ".join(self.candidates)})
        )"""


@router.get("/products/search")
async def search_products(
    q: str = Query(..., min_length=1),
    tipo: str | None = Query(None),
    marca: str | None = Query(None),
    contenido: Decimal | None = Query(None, gt=0),
    unidad: Literal["g", "ml", "u"] | None = Query(None),
    provincia: str | None = Query(None),
    localidad: str | None = Query(None),
    cadenas: list[str] | None = Query(None),
    location: Location | None = Depends(optional_location),
    limit: int = Query(50, ge=1, le=100),
):
    """Products with a price in the latest published day, ranked for reading.

    An exact EAN comes first, then products of the type the query names
    ("leche" before dulce de leche), then of the longest type it names as
    whole words ("serenisima leche"), then products sold by more chains.

    Each product carries its lowest store price in that day, the date that
    price was observed, that price per kg or l, how many stores carry it, and
    the reasons its prices may compare poorly (``api.coverage``).  The store filters
    (province, locality, chains, and ``radius`` km around ``lat`` and
    ``lon``) choose which stores count; a product none of them carries has
    no "from" price.  They do not change the order.
    """
    matching = _Matching(q)
    if matching.empty:
        return []
    param = matching.param
    q_ref = matching.query()
    conditions = [
        *matching.conditions,
        *matching.attribute_filters(tipo, marca, contenido, unidad).values(),
    ]

    # Each result's prices are read after ranking, for the returned page only.
    prices_where = ["f.producto_sk = r.producto_sk", "f.fecha_id = r.fecha_id"]
    joins = []
    if provincia:
        joins.append("JOIN dim_provincia pr ON pr.provincia_sk = s.provincia_sk")
        prices_where.append(f"pr.nombre = {param(provincia)}")
    if localidad:
        prices_where.append(f"s.sucursales_localidad = {param(localidad)}")
    if cadenas:
        joins.append("JOIN dim_comercio c ON c.comercio_sk = s.comercio_sk")
        prices_where.append(f"c.comercio_bandera_nombre = ANY({param(cadenas)}::text[])")
    if location:
        prices_where.append(within_sql(location, matching.params))
    prices_source = "fact_precios f"
    if len(prices_where) > 2:
        prices_source += " JOIN dim_sucursal s ON s.sucursal_sk = f.sucursal_sk " + " ".join(joins)
    prices = store_priced_facts(
        prices_source, " AND ".join(prices_where), f"{PRICE_DATE_SQL} AS fecha_precio"
    )

    rows = await fetch(
        f"""
        WITH {matching.candidates_cte()},
        resultados AS (
            SELECT p.producto_sk, p.id_producto, p.es_ean, p.nombre,
                   p.productos_descripcion, p.productos_marca, p.tipo, p.variante,
                   p.contenido, p.contenido_unidad,
                   {LATEST_PUBLISHED_FECHA_ID} AS fecha_id,
                   ROW_NUMBER() OVER (
                       ORDER BY {matching.is_ean} IS TRUE DESC,
                                COALESCE(p.tipo = pipeline_product_type({q_ref}), FALSE) DESC,
                                CASE
                                    WHEN POSITION(
                                        ' ' || pipeline_normalize_text(p.tipo) || ' '
                                        IN ' ' || pipeline_normalize_text({q_ref}) || ' '
                                    ) > 0 THEN LENGTH(p.tipo)
                                    ELSE 0
                                END DESC,
                                p.cadenas_fuente DESC NULLS LAST,
                                p.nombre,
                                p.producto_sk
                   ) AS orden
            FROM candidatos p
            WHERE {" AND ".join(conditions)}
            ORDER BY orden
            LIMIT {param(limit)}
        )
        SELECT r.*, precios.*
        FROM resultados r
        CROSS JOIN LATERAL (
            SELECT MIN(o.precio) AS precio_desde,
                   (ARRAY_AGG(o.fecha_precio ORDER BY o.precio, o.fecha_precio DESC)
                       FILTER (WHERE o.precio IS NOT NULL))[1] AS fecha_precio_desde,
                   -- One fact per product, store and day.
                   COUNT(o.precio) AS sucursales
            FROM ({prices}) o
        ) precios
        ORDER BY r.orden
        """,
        *matching.params,
    )
    return [
        {
            "producto_sk": r["producto_sk"],
            "id_producto": r["id_producto"],
            "es_ean": r["es_ean"],
            "nombre": r["nombre"],
            "descripcion": r["productos_descripcion"],
            "marca": r["productos_marca"],
            "tipo": r["tipo"],
            "variante": r["variante"],
            "contenido": r["contenido"],
            "contenido_unidad": r["contenido_unidad"],
            "precio_desde": r["precio_desde"],
            "precio_unitario_desde": unit_price(
                r["precio_desde"], r["contenido"], r["contenido_unidad"]
            ),
            "fecha_precio_desde": r["fecha_precio_desde"],
            "sucursales": r["sucursales"],
            "cobertura": coverage_notes(r["es_ean"], r["id_producto"], r["sucursales"]),
        }
        for r in rows
    ]


@router.get("/products/search/facets")
async def search_facets(
    q: str = Query(..., min_length=1),
    tipo: str | None = Query(None),
    marca: str | None = Query(None),
    contenido: Decimal | None = Query(None, gt=0),
    unidad: Literal["g", "ml", "u"] | None = Query(None),
):
    """Types, brands and sizes of the products a search lists, to narrow it.

    Each value counts the products the search would list with it chosen, so
    a facet applies the other filters but not its own: choosing a brand
    keeps the other brands available.  Brands are grouped without regard to
    case or accents ("Cepita" and "CEPITA"), under their most common
    spelling as the product names write it.  A blank brand and a single unit,
    which names leave out, are not choices.
    """
    empty = {"tipos": [], "marcas": [], "tamanos": []}
    matching = _Matching(q)
    if matching.empty:
        return empty
    filters = matching.attribute_filters(tipo, marca, contenido, unidad)

    def others(facet: str) -> str:
        return " AND ".join(c for name, c in filters.items() if name != facet) or "TRUE"

    # facet: (filter it ignores, grouping keys, label, size and unit columns)
    facet_queries = {
        "tipos": ("tipo", ["p.tipo"], "p.tipo", "NULL::numeric", "NULL::text"),
        "marcas": (
            "marca",
            ["NULLIF(pipeline_normalize_text(p.productos_marca), '')"],
            "INITCAP(MODE() WITHIN GROUP (ORDER BY BTRIM(p.productos_marca)))",
            "NULL::numeric",
            "NULL::text",
        ),
        "tamanos": (
            "tamano",
            ["p.contenido", "p.contenido_unidad"],
            "pipeline_format_size(p.contenido, p.contenido_unidad)",
            "p.contenido",
            "p.contenido_unidad",
        ),
    }
    selects = []
    for kind, (facet, keys, label, size, unit) in facet_queries.items():
        selects.append(
            f"""(
            SELECT '{kind}' AS faceta, {label} AS valor, {size} AS contenido,
                   {unit} AS unidad, COUNT(*) AS productos
            FROM coincidencias p
            WHERE {others(facet)} AND {" AND ".join(f"{key} IS NOT NULL" for key in keys)}
            GROUP BY {", ".join(keys)}
            HAVING {label} IS NOT NULL
            ORDER BY productos DESC, valor
            LIMIT {MAX_FACET_VALUES}
        )"""
        )

    rows = await fetch(
        f"""
        WITH {matching.candidates_cte()},
        coincidencias AS MATERIALIZED (
            SELECT p.* FROM candidatos p WHERE {" AND ".join(matching.conditions)}
        )
        {" UNION ALL ".join(selects)}
        """,
        *matching.params,
    )
    facets = empty
    for r in rows:
        value = {"valor": r["valor"], "productos": r["productos"]}
        if r["faceta"] == "tamanos":
            value |= {"contenido": r["contenido"], "unidad": r["unidad"]}
        facets[r["faceta"]].append(value)
    return facets


@router.get("/products/{producto_sk}")
async def get_product(producto_sk: int):
    """A product as the catalog names it, next to what its source published.

    ``publicado`` keeps the description, brand and size as the chains wrote
    them, before any curated correction (``corregido``).  ``codigos`` are the
    source identifiers: the EAN, or for a product without a valid EAN the
    chain code and the chain (``id_comercio``, ``id_bandera``) that scopes it.
    """
    rows = await fetch(
        """
        SELECT
            v.producto_sk, v.nombre, v.productos_descripcion AS descripcion,
            v.productos_marca AS marca, v.tipo, v.variante, v.contenido,
            v.contenido_unidad, v.cadenas_fuente, v.corregido,
            p.productos_descripcion AS publicado_descripcion,
            p.productos_marca AS publicado_marca,
            p.productos_cantidad_presentacion AS publicado_cantidad,
            p.productos_unidad_medida_presentacion AS publicado_unidad,
            v.producto_identity, v.id_producto, v.es_ean,
            v.source_id_comercio, v.source_id_bandera,
            c.comercio_bandera_nombre AS source_cadena
        FROM v_producto v
        JOIN dim_producto p ON p.producto_sk = v.producto_sk
        LEFT JOIN dim_comercio c
            ON c.id_comercio = v.source_id_comercio AND c.id_bandera = v.source_id_bandera
        WHERE v.producto_sk = $1
        """,
        producto_sk,
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Producto inexistente")
    r = rows[0]
    return {
        "producto_sk": r["producto_sk"],
        "nombre": r["nombre"],
        "descripcion": r["descripcion"],
        "marca": r["marca"],
        "tipo": r["tipo"],
        "variante": r["variante"],
        "contenido": r["contenido"],
        "contenido_unidad": r["contenido_unidad"],
        "cadenas_fuente": r["cadenas_fuente"],
        "corregido": bool(r["corregido"]),
        "cobertura": coverage_notes(r["es_ean"], r["id_producto"]),
        "publicado": {
            "descripcion": r["publicado_descripcion"],
            "marca": r["publicado_marca"],
            "cantidad": r["publicado_cantidad"],
            "unidad": r["publicado_unidad"],
        },
        "codigos": {
            "identidad": r["producto_identity"],
            "id_producto": r["id_producto"],
            "es_ean": r["es_ean"],
            "id_comercio": r["source_id_comercio"],
            "id_bandera": r["source_id_bandera"],
            "cadena": r["source_cadena"],
        },
    }
