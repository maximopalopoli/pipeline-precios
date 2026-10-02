"""Product search endpoint."""

import re
import unicodedata
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Query

from api.db import fetch
from api.price_date import PRICE_DATE_SQL
from api.pricing import store_priced_facts
from api.published_stores import LATEST_PUBLISHED_FECHA_ID

router = APIRouter()

# Words shorter than this match almost every product ("DE", "X", the "5" of
# "1.5"); they are left out of the query.
MIN_WORD_LENGTH = 2
MAX_WORDS = 8
# pg_trgm indexes a LIKE pattern only from three letters on.
TRIGRAM_LENGTH = 3
# Kilometres per degree in AMBA, as the other location filters approximate.
KM_PER_DEGREE_LAT = 111.0
KM_PER_DEGREE_LON = 85.0


def query_words(q: str) -> list[str]:
    """The query's words, normalized as pipeline_normalize_text does in SQL."""
    without_accents = "".join(
        c for c in unicodedata.normalize("NFD", q.upper()) if unicodedata.category(c) != "Mn"
    )
    words = re.sub(r"[^A-Z0-9]+", " ", without_accents).split()
    unique = list(dict.fromkeys(w for w in words if len(w) >= MIN_WORD_LENGTH))
    return unique[:MAX_WORDS]


@router.get("/products/search")
async def search_products(
    q: str = Query(..., min_length=1),
    tipo: str | None = Query(None),
    marca: str | None = Query(None),
    contenido: Decimal | None = Query(None, gt=0),
    unidad: Literal["g", "ml", "u"] | None = Query(None),
    lat: float | None = Query(None, ge=-90, le=90),
    lon: float | None = Query(None, ge=-180, le=180),
    radius: float = Query(1, ge=0.5, le=50),
    limit: int = Query(50, ge=1, le=100),
):
    """Products with a price in the latest published day, ranked for reading.

    An exact EAN comes first, then products of the type the query names
    ("leche" before dulce de leche), then of the longest type it names as
    whole words ("serenisima leche"), then products sold by more chains.

    Each product carries its lowest store price in that day, the date that
    price was observed, and how many stores carry it; with ``lat`` and
    ``lon``, only stores within ``radius`` km count, and a product none of
    them carries has no "from" price.  Location does not change the order.
    """
    words = query_words(q)
    ean = q.strip() if q.strip().isdigit() else None
    if not words and ean is None:
        return []

    params: list = [q, ean]

    def param(value) -> str:
        params.append(value)
        return f"${len(params)}"

    patterns = {word: param(f"%{word}%") for word in words}
    # Candidates come from indexes: the voted attributes through trigrams
    # (words of at least three letters), the type the query names when it
    # has no such word, and the few products with an override, whose
    # corrected text is checked below.
    type_named = None
    indexed = [pattern for word, pattern in patterns.items() if len(word) >= TRIGRAM_LENGTH]
    candidates = ["SELECT producto_sk FROM dim_producto WHERE producto_identity = 'ean:' || $2"]
    if indexed:
        text = (
            "pipeline_product_search_text("
            "productos_descripcion, productos_marca, tipo, variante)"
        )
        candidates.append(
            "SELECT producto_sk FROM dim_producto WHERE "
            + " AND ".join(f"{text} LIKE {pattern}" for pattern in indexed)
        )
    elif words:
        candidates.append(
            "SELECT producto_sk FROM dim_producto WHERE tipo = pipeline_product_type($1)"
        )
        type_named = "p.tipo = pipeline_product_type($1)"
    if words:
        candidates.append(
            "SELECT d.producto_sk FROM producto_overrides o "
            "JOIN dim_producto d ON d.producto_identity = o.producto_identity"
        )

    conditions = [
        f"""EXISTS (
            SELECT 1 FROM fact_precios f
            WHERE f.producto_sk = p.producto_sk
              AND f.fecha_id = {LATEST_PUBLISHED_FECHA_ID}
        )"""
    ]
    if words:
        matches = " AND ".join(f"p.texto_busqueda LIKE {pattern}" for pattern in patterns.values())
        if type_named:
            matches = f"{type_named} AND {matches}"
        conditions.append(f"(p.producto_identity = 'ean:' || $2 OR ({matches}))")
    if tipo:
        conditions.append(f"p.tipo = {param(tipo)}")
    if marca:
        conditions.append(
            f"pipeline_normalize_text(p.productos_marca) = pipeline_normalize_text({param(marca)})"
        )
    if contenido is not None:
        conditions.append(f"p.contenido = {param(contenido)}")
    if unidad:
        conditions.append(f"p.contenido_unidad = {param(unidad)}")

    # Each result's prices are read after ranking, for the returned page only.
    prices_where = "f.producto_sk = r.producto_sk AND f.fecha_id = r.fecha_id"
    prices_source = "fact_precios f"
    if lat is not None and lon is not None:
        prices_source += " JOIN dim_sucursal s ON s.sucursal_sk = f.sucursal_sk"
        lat_km = f"(s.sucursales_latitud::float8 - {param(lat)}::float8) * {KM_PER_DEGREE_LAT}"
        lon_km = f"(s.sucursales_longitud::float8 - {param(lon)}::float8) * {KM_PER_DEGREE_LON}"
        prices_where += f" AND SQRT(POW({lat_km}, 2) + POW({lon_km}, 2)) <= {param(radius)}"
    prices = store_priced_facts(
        prices_source, prices_where, f"{PRICE_DATE_SQL} AS fecha_precio"
    )

    # Materialized so that the text checks run on the candidates only; inlined,
    # the planner applied them to every product (about 700 ms).
    rows = await fetch(
        f"""
        WITH candidatos AS MATERIALIZED (
            SELECT p.*
            FROM v_producto p
            WHERE p.producto_sk IN ({" UNION ".join(candidates)})
        ),
        resultados AS (
            SELECT p.producto_sk, p.id_producto, p.es_ean, p.nombre,
                   p.productos_descripcion, p.productos_marca, p.tipo, p.variante,
                   p.contenido, p.contenido_unidad,
                   {LATEST_PUBLISHED_FECHA_ID} AS fecha_id,
                   ROW_NUMBER() OVER (
                       ORDER BY p.producto_identity = 'ean:' || $2 IS TRUE DESC,
                                COALESCE(p.tipo = pipeline_product_type($1), FALSE) DESC,
                                CASE
                                    WHEN POSITION(
                                        ' ' || pipeline_normalize_text(p.tipo) || ' '
                                        IN ' ' || pipeline_normalize_text($1) || ' '
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
        *params,
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
            "fecha_precio_desde": r["fecha_precio_desde"],
            "sucursales": r["sucursales"],
        }
        for r in rows
    ]
