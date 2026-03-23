"""GET /api/health — last ingestion status."""

from fastapi import APIRouter

from api.db import fetchrow

router = APIRouter()


@router.get("/health")
async def get_health():
    row = await fetchrow("""
        SELECT ir.status, ir.started_at, rf.fecha_datos
        FROM ingestion_runs ir
        JOIN raw_files rf ON ir.raw_file_id = rf.id
        ORDER BY ir.started_at DESC
        LIMIT 1
    """)
    if not row:
        return {"status": "no_data", "started_at": None, "fecha_datos": None}
    return {
        "status": row["status"],
        "started_at": row["started_at"].isoformat() if row["started_at"] else None,
        "fecha_datos": row["fecha_datos"].isoformat() if row["fecha_datos"] else None,
    }
