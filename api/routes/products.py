"""Product search endpoint."""

from fastapi import APIRouter, Query

from api.db import fetch

router = APIRouter()


@router.get("/products/search")
async def search_products(q: str = Query(..., min_length=1)):
    rows = await fetch(
        """
        SELECT producto_sk, id_producto, productos_descripcion, productos_marca
        FROM dim_producto
        WHERE productos_descripcion ILIKE $1
           OR productos_marca        ILIKE $1
           OR id_producto             = $2
        LIMIT 50
        """,
        f"%{q}%",
        q,
    )
    return [
        {
            "producto_sk": r["producto_sk"],
            "id_producto": r["id_producto"],
            "descripcion": r["productos_descripcion"],
            "marca": r["productos_marca"],
        }
        for r in rows
    ]
