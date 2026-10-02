"""Product search endpoint."""

import re
import unicodedata
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Query

from api.db import fetch
from api.published_stores import LATEST_PUBLISHED_FECHA_ID

router = APIRouter()

# Words shorter than this match almost every product ("DE", "X", the "5" of
# "1.5"); they are left out of the query.
MIN_WORD_LENGTH = 2
MAX_WORDS = 8
# pg_trgm indexes a LIKE pattern only from three letters on.
TRIGRAM_LENGTH = 3


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
    limit: int = Query(50, ge=1, le=100),
):
    """Products with a price in the latest published day, ranked for reading.

    An exact EAN comes first, then products of the type the query starts
    with ("leche" before dulce de leche), then of the longest type it names
    anywhere ("serenisima leche"), then products sold by more chains.
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

    # Materialized so that the text checks run on the candidates only; inlined,
    # the planner applied them to every product (about 700 ms).
    rows = await fetch(
        f"""
        WITH candidatos AS MATERIALIZED (
            SELECT p.*
            FROM v_producto p
            WHERE p.producto_sk IN ({" UNION ".join(candidates)})
        )
        SELECT p.producto_sk, p.id_producto, p.es_ean, p.nombre,
               p.productos_descripcion, p.productos_marca, p.tipo, p.variante,
               p.contenido, p.contenido_unidad
        FROM candidatos p
        WHERE {" AND ".join(conditions)}
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
        LIMIT {param(limit)}
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
        }
        for r in rows
    ]
