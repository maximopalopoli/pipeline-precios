"""Observation date of a published price, including carried rows."""

PRICE_DATE_SQL = """TO_CHAR(
    TO_DATE(COALESCE(f.fecha_observada_id, f.fecha_id)::text, 'YYYYMMDD'),
    'YYYY-MM-DD'
)"""
