"""GET /api/health — last ingestion status."""

from fastapi import APIRouter

from api.db import fetchrow

router = APIRouter()


def _reconciliation_response(row: dict) -> dict | None:
    fields = (
        "rows_productos_source",
        "rows_productos_raw",
        "rows_productos_selected",
        "rows_productos_out_of_scope",
        "rows_productos_store_unlisted",
        "rows_productos_valid",
        "rows_productos_rejected",
        "rows_productos_without_keys",
        "rows_productos_deduplicated",
        "rows_fact_pending",
        "rows_fact_published",
        "rows_fact_carried_forward",
        "rows_fact_snapshot",
    )
    if all(row.get(field) is None for field in fields):
        return None
    return {field: row.get(field) for field in fields}


def _ingestion_notice(attempt: dict | None, publication: dict | None) -> dict | None:
    """Describe a full ingestion started after the last successful publication."""
    if not attempt or attempt["status"] not in {"failed", "running"}:
        return None
    if publication and attempt["started_at"] <= publication["finished_at"]:
        return None
    return {
        "status": attempt["status"],
        "fecha_datos": attempt["fecha_datos"].isoformat(),
        "started_at": attempt["started_at"].isoformat(),
    }


@router.get("/health")
async def get_health():
    row = await fetchrow("""
        SELECT
            ir.status,
            ir.started_at,
            rf.fecha_datos,
            ir.is_partial,
            ir.regional_scope,
            ir.rows_productos_source,
            ir.rows_productos_raw,
            ir.rows_productos_selected,
            ir.rows_productos_out_of_scope,
            ir.rows_productos_store_unlisted,
            ir.rows_productos_valid,
            ir.rows_productos_rejected,
            ir.rows_productos_without_keys,
            ir.rows_productos_deduplicated,
            ir.rows_fact_pending,
            ir.rows_fact_published,
            ir.rows_fact_carried_forward,
            ir.rows_fact_snapshot
        FROM ingestion_runs ir
        JOIN raw_files rf ON ir.raw_file_id = rf.id
        ORDER BY ir.started_at DESC, ir.id DESC
        LIMIT 1
    """)
    if not row:
        return {
            "status": "no_data",
            "started_at": None,
            "fecha_datos": None,
            "is_partial": None,
            "regional_scope": None,
            "reconciliacion": None,
            "ultima_publicacion": None,
            "aviso_ingesta": None,
        }

    publication = await fetchrow("""
        SELECT rf.fecha_datos, ir.finished_at
        FROM ingestion_runs ir
        JOIN raw_files rf ON ir.raw_file_id = rf.id
        WHERE ir.status = 'completed'
          AND ir.is_partial = FALSE
          AND EXISTS (SELECT 1 FROM fact_precios f WHERE f.run_id = ir.id)
          AND ir.finished_at IS NOT NULL
        ORDER BY rf.fecha_datos DESC, ir.finished_at DESC, ir.id DESC
        LIMIT 1
    """)
    attempt = await fetchrow("""
        SELECT ir.status, ir.started_at, rf.fecha_datos
        FROM ingestion_runs ir
        JOIN raw_files rf ON ir.raw_file_id = rf.id
        WHERE ir.is_partial = FALSE
        ORDER BY ir.started_at DESC, ir.id DESC
        LIMIT 1
    """)
    return {
        "status": row["status"],
        "started_at": row["started_at"].isoformat() if row["started_at"] else None,
        "fecha_datos": row["fecha_datos"].isoformat() if row["fecha_datos"] else None,
        "is_partial": row["is_partial"],
        "regional_scope": row["regional_scope"],
        "reconciliacion": _reconciliation_response(row),
        "ultima_publicacion": {
            "fecha_datos": publication["fecha_datos"].isoformat(),
            "finished_at": publication["finished_at"].isoformat(),
        } if publication else None,
        "aviso_ingesta": _ingestion_notice(attempt, publication),
    }
