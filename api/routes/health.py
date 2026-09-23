"""GET /api/health — last ingestion status."""

from fastapi import APIRouter

from api.db import fetchrow

router = APIRouter()


def _reconciliation_response(row: dict) -> dict | None:
    fields = (
        "rows_productos_source",
        "rows_productos_raw",
        "rows_productos_selected",
        "rows_productos_valid",
        "rows_productos_rejected",
        "rows_productos_without_keys",
        "rows_productos_deduplicated",
        "rows_fact_pending",
        "rows_fact_published",
    )
    if all(row[field] is None for field in fields):
        return None
    return {field: row[field] for field in fields}


@router.get("/health")
async def get_health():
    row = await fetchrow("""
        SELECT
            ir.status,
            ir.started_at,
            rf.fecha_datos,
            ir.is_partial,
            ir.rows_productos_source,
            ir.rows_productos_raw,
            ir.rows_productos_selected,
            ir.rows_productos_valid,
            ir.rows_productos_rejected,
            ir.rows_productos_without_keys,
            ir.rows_productos_deduplicated,
            ir.rows_fact_pending,
            ir.rows_fact_published
        FROM ingestion_runs ir
        JOIN raw_files rf ON ir.raw_file_id = rf.id
        ORDER BY ir.started_at DESC
        LIMIT 1
    """)
    if not row:
        return {
            "status": "no_data",
            "started_at": None,
            "fecha_datos": None,
            "is_partial": None,
            "reconciliacion": None,
        }
    return {
        "status": row["status"],
        "started_at": row["started_at"].isoformat() if row["started_at"] else None,
        "fecha_datos": row["fecha_datos"].isoformat() if row["fecha_datos"] else None,
        "is_partial": row["is_partial"],
        "reconciliacion": _reconciliation_response(row),
    }
