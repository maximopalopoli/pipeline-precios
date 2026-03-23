"""Price evolution endpoint."""

from fastapi import APIRouter, Query

from api.db import fetch

router = APIRouter()


@router.get("/evolucion")
async def get_evolucion(
    producto_sk: int = Query(...),
    provincia: str | None = Query(None),
):
    conditions = ["a.producto_sk = $1"]
    params: list = [producto_sk]
    idx = 2

    if provincia:
        conditions.append(f"pr.nombre = ${idx}")
        params.append(provincia)
        idx += 1

    where = " AND ".join(conditions)

    rows = await fetch(
        f"""
        SELECT
            f.fecha,
            ROUND(AVG(a.precio_lista_avg)::numeric, 2) AS precio_lista,
            ROUND(AVG(a.precio_promo_avg)::numeric, 2) AS precio_promo,
            SUM(a.cantidad_obs)                         AS observaciones
        FROM agg_precios_diarios a
        JOIN dim_fecha     f  ON a.fecha_id     = f.fecha_id
        JOIN dim_provincia pr ON a.provincia_sk  = pr.provincia_sk
        WHERE {where}
        GROUP BY f.fecha
        ORDER BY f.fecha
        """,
        *params,
    )
    return [
        {
            "fecha": r["fecha"].isoformat(),
            "precio_lista": float(r["precio_lista"]) if r["precio_lista"] else None,
            "precio_promo": float(r["precio_promo"]) if r["precio_promo"] else None,
            "observaciones": r["observaciones"],
        }
        for r in rows
    ]
