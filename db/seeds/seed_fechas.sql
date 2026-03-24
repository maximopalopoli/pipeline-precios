-- seed_fechas.sql
-- Populate dim_fecha for 2026 (pipeline working range)

INSERT INTO dim_fecha (fecha_id, fecha, anio, mes, trimestre, semana_iso, dia_semana, es_fin_semana)
SELECT
    TO_CHAR(d, 'YYYYMMDD')::INT        AS fecha_id,
    d::DATE                             AS fecha,
    EXTRACT(YEAR    FROM d)::SMALLINT   AS anio,
    EXTRACT(MONTH   FROM d)::SMALLINT   AS mes,
    EXTRACT(QUARTER FROM d)::SMALLINT   AS trimestre,
    EXTRACT(WEEK    FROM d)::SMALLINT   AS semana_iso,
    EXTRACT(ISODOW  FROM d)::SMALLINT   AS dia_semana,   -- 1=Monday, 7=Sunday
    EXTRACT(ISODOW  FROM d) >= 6        AS es_fin_semana
FROM generate_series(
    '2026-01-01'::DATE,
    '2026-12-31'::DATE,
    '1 day'::INTERVAL
) AS d
ON CONFLICT (fecha_id) DO NOTHING;
