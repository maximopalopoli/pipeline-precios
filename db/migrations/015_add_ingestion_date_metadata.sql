-- Keep the distinct dates involved in one ingestion attempt auditable.
-- Existing runs used raw_files.fecha_datos as their only date, so it is the
-- best available historical value for both requested and logical dates.

ALTER TABLE ingestion_runs
    ADD COLUMN IF NOT EXISTS fecha_solicitada DATE,
    ADD COLUMN IF NOT EXISTS fecha_logica DATE,
    ADD COLUMN IF NOT EXISTS fecha_fuente DATE;

UPDATE ingestion_runs runs
SET fecha_solicitada = raw.fecha_datos,
    fecha_logica = raw.fecha_datos
FROM raw_files raw
WHERE raw.id = runs.raw_file_id
  AND (runs.fecha_solicitada IS NULL OR runs.fecha_logica IS NULL);

CREATE INDEX IF NOT EXISTS idx_ingestion_runs_fecha_logica
    ON ingestion_runs (fecha_logica);
