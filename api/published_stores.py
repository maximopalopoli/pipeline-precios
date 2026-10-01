"""SQL predicates for stores with offers in the latest published day."""

# Completed runs with published facts identify the current day without scanning
# the fact table for its maximum date. Older fact days may still be retained.
LATEST_PUBLISHED_FECHA_ID = """(
    SELECT TO_CHAR(MAX(rf.fecha_datos), 'YYYYMMDD')::int
    FROM ingestion_runs r
    JOIN raw_files rf ON rf.id = r.raw_file_id
    WHERE r.status = 'completed' AND r.rows_fact_published > 0
)"""

STORE_HAS_PUBLISHED_PRICE = f"""EXISTS (
    SELECT 1
    FROM fact_precios f
    WHERE f.sucursal_sk = s.sucursal_sk
      AND f.fecha_id = {LATEST_PUBLISHED_FECHA_ID}
)"""
