"""Filter endpoints for dropdowns."""

from fastapi import APIRouter, Depends, Query

from api.chain_names import chain_name
from api.db import fetch
from api.location import Location, optional_location, within_sql
from api.published_stores import STORE_HAS_PUBLISHED_PRICE

router = APIRouter()


@router.get("/filters/provinces")
async def get_provinces():
    rows = await fetch(
        f"""
        SELECT DISTINCT pr.nombre
        FROM dim_sucursal s
        JOIN dim_provincia pr ON s.provincia_sk = pr.provincia_sk
        WHERE {STORE_HAS_PUBLISHED_PRICE}
        ORDER BY 1
        """
    )
    return [r["nombre"] for r in rows]


@router.get("/filters/chains")
async def get_chains():
    rows = await fetch(
        f"""
        SELECT DISTINCT c.comercio_bandera_nombre
        FROM dim_sucursal s
        JOIN dim_comercio c ON s.comercio_sk = c.comercio_sk
        WHERE {STORE_HAS_PUBLISHED_PRICE}
        ORDER BY 1
        """
    )
    return [r["comercio_bandera_nombre"] for r in rows]


@router.get("/filters/chain-options")
async def get_chain_options(location: Location | None = Depends(optional_location)):
    """Chains to choose among, with the stores each has near the location.

    ``valor`` is the value the ``cadenas`` filters take and ``nombre`` the
    name shoppers know it by.  With ``lat`` and ``lon`` only chains with a
    store within ``radius`` km are listed; the most stores come first.
    """
    params: list = []
    conditions = [STORE_HAS_PUBLISHED_PRICE, "c.comercio_bandera_nombre IS NOT NULL"]
    if location:
        conditions.append(within_sql(location, params))
    rows = await fetch(
        f"""
        SELECT c.comercio_bandera_nombre AS valor,
               MIN(c.id_comercio) AS id_comercio,
               MIN(c.id_bandera) AS id_bandera,
               COUNT(*) AS sucursales
        FROM dim_sucursal s
        JOIN dim_comercio c ON s.comercio_sk = c.comercio_sk
        WHERE {" AND ".join(conditions)}
        GROUP BY c.comercio_bandera_nombre
        """,
        *params,
    )
    options = [
        {
            "valor": r["valor"],
            "nombre": chain_name(r["id_comercio"], r["id_bandera"], r["valor"]),
            "sucursales": r["sucursales"],
        }
        for r in rows
    ]
    return sorted(options, key=lambda o: (-o["sucursales"], o["nombre"]))


@router.get("/filters/localities")
async def get_localities(province: str = Query(...)):
    rows = await fetch(
        f"""
        SELECT DISTINCT s.sucursales_localidad
        FROM dim_sucursal s
        JOIN dim_provincia pr ON s.provincia_sk = pr.provincia_sk
        WHERE pr.nombre = $1
          AND s.sucursales_localidad IS NOT NULL
          AND {STORE_HAS_PUBLISHED_PRICE}
        ORDER BY 1
        """,
        province,
    )
    return [r["sucursales_localidad"] for r in rows]

