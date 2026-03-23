"""Filter endpoints for dropdowns and date ranges."""

from fastapi import APIRouter, Query

from api.db import fetch, fetchrow

router = APIRouter()


@router.get("/filters/provinces")
async def get_provinces():
    rows = await fetch("SELECT nombre FROM dim_provincia ORDER BY nombre")
    return [r["nombre"] for r in rows]


@router.get("/filters/chains")
async def get_chains():
    rows = await fetch(
        "SELECT DISTINCT comercio_bandera_nombre FROM dim_comercio ORDER BY 1"
    )
    return [r["comercio_bandera_nombre"] for r in rows]


@router.get("/filters/localities")
async def get_localities(province: str = Query(...)):
    rows = await fetch(
        """
        SELECT DISTINCT s.sucursales_localidad
        FROM dim_sucursal s
        JOIN dim_provincia pr ON s.provincia_sk = pr.provincia_sk
        WHERE pr.nombre = $1
          AND s.sucursales_localidad IS NOT NULL
        ORDER BY 1
        """,
        province,
    )
    return [r["sucursales_localidad"] for r in rows]


@router.get("/filters/date-range")
async def get_date_range():
    row = await fetchrow("""
        SELECT MIN(fecha) AS min_fecha, MAX(fecha) AS max_fecha
        FROM dim_fecha
        JOIN agg_precios_diarios USING (fecha_id)
    """)
    if not row or row["max_fecha"] is None:
        return {"min_fecha": None, "max_fecha": None}
    return {
        "min_fecha": row["min_fecha"].isoformat(),
        "max_fecha": row["max_fecha"].isoformat(),
    }
