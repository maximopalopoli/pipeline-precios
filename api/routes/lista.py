"""Shopping list totals per nearby store."""

from fastapi import APIRouter, Depends, Query

from api.db import fetch, fetchrow
from api.location import Location, bind, distance_km_sql, required_location, within_sql
from api.price_date import PRICE_DATE_SQL
from api.pricing import OFFER_PRICE_COLUMNS, PROMO_JOINS
from api.published_stores import LATEST_PUBLISHED_FECHA_ID

router = APIRouter()

MAX_LIST_ITEMS = 50
MAX_STORES = 50


def store_totals(products: list[int], rows: list[dict]) -> list[dict]:
    """Total the list in each store, most complete first, then cheapest.

    A store carries an item when it has a price for it.  ``total`` adds the
    prices paid without meeting any condition; ``total_condicional`` adds,
    for each item, the lower conditional promotion when there is one, so it
    is only reached by meeting every condition and is ``None`` when no item
    has one.  Totals are compared only among stores carrying the same number
    of items, so a cheaper but incomplete basket never ranks above a
    complete one.
    """
    stores: dict[int, dict] = {}
    for row in rows:
        store = stores.setdefault(
            row["sucursal_sk"],
            {
                "sucursal_sk": row["sucursal_sk"],
                "cadena": row["cadena"],
                "sucursal": row["sucursal"],
                "direccion": row["direccion"],
                "localidad": row["localidad"],
                "lat": row["lat"],
                "lon": row["lon"],
                "distancia_km": row["distancia_km"],
                "items": [],
            },
        )
        store["items"].append(
            {
                key: row[key]
                for key in (
                    "producto_sk", "precio_lista", "mejor_precio", "precio_promo",
                    "leyenda_promo", "precio_condicional", "leyenda_condicional",
                    "condicion", "fecha_precio",
                )
            }
        )

    for store in stores.values():
        items = store["items"]
        carried = {item["producto_sk"] for item in items}
        conditional = [item for item in items if item["precio_condicional"] is not None]
        store["cubiertos"] = len(carried)
        store["productos"] = len(products)
        store["faltantes"] = [sk for sk in products if sk not in carried]
        store["total"] = sum(item["mejor_precio"] for item in items)
        store["total_condicional"] = (
            sum(
                item["mejor_precio"]
                if item["precio_condicional"] is None
                else item["precio_condicional"]
                for item in items
            )
            if conditional
            else None
        )
        store["condicionales"] = len(conditional)

    ranked = sorted(
        stores.values(),
        key=lambda s: (-s["cubiertos"], s["total"], s["distancia_km"], s["sucursal_sk"]),
    )
    return ranked[:MAX_STORES]


@router.get("/lista/totales")
async def get_list_totals(
    producto_sk: list[int] = Query(..., min_length=1, max_length=MAX_LIST_ITEMS),
    location: Location = Depends(required_location),
):
    """Each nearby store's total for the list, with its coverage and gaps.

    Prices come from the latest published day, carried prices included with
    their observation date.  A product listed twice counts once.
    """
    products = list(dict.fromkeys(producto_sk))
    fecha = await fetchrow(f"SELECT {LATEST_PUBLISHED_FECHA_ID} AS fecha_id")
    if not fecha or fecha["fecha_id"] is None:
        return []

    params: list = []
    distance = distance_km_sql(location, params)
    conditions = [
        f"f.fecha_id = {bind(params, fecha['fecha_id'])}",
        f"f.producto_sk = ANY({bind(params, products)}::int[])",
        within_sql(location, params),
    ]
    rows = await fetch(
        f"""
        SELECT * FROM (
            SELECT
                f.sucursal_sk,
                f.producto_sk,
                c.comercio_bandera_nombre AS cadena,
                s.sucursales_nombre       AS sucursal,
                NULLIF(CONCAT_WS(' ', s.sucursales_calle, s.sucursales_numero), '') AS direccion,
                s.sucursales_localidad    AS localidad,
                s.sucursales_latitud      AS lat,
                s.sucursales_longitud     AS lon,
                ROUND({distance}::numeric, 1) AS distancia_km,
                f.precio_lista,
                {PRICE_DATE_SQL}          AS fecha_precio,
                {OFFER_PRICE_COLUMNS}
            FROM fact_precios f
            JOIN dim_sucursal s ON f.sucursal_sk = s.sucursal_sk
            JOIN dim_comercio c ON s.comercio_sk = c.comercio_sk
            {PROMO_JOINS}
            WHERE {" AND ".join(conditions)}
        ) ofertas
        WHERE mejor_precio IS NOT NULL
        """,
        *params,
    )
    return store_totals(products, [dict(r) for r in rows])
