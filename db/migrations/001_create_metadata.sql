-- 001_create_metadata.sql
-- Pipeline operational traceability tables

CREATE TABLE IF NOT EXISTS raw_files (
    id              SERIAL PRIMARY KEY,
    filename        TEXT        NOT NULL,
    url             TEXT        NOT NULL,
    sha256          TEXT        NOT NULL UNIQUE,
    size_bytes      BIGINT      NOT NULL,
    fecha_datos     DATE        NOT NULL,
    downloaded_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_raw_files_sha256      ON raw_files (sha256);
CREATE INDEX IF NOT EXISTS idx_raw_files_fecha_datos ON raw_files (fecha_datos);

-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS ingestion_runs (
    id                  SERIAL PRIMARY KEY,
    raw_file_id         INT         NOT NULL REFERENCES raw_files (id),

    -- staging counts
    inner_zips_count    INT,
    rows_comercios      INT,
    rows_sucursales     INT,
    rows_productos_raw  INT,

    -- quality / load counts
    rows_valid          INT,
    rows_rejected       INT,
    rows_loaded         INT,

    -- timestamps
    started_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at         TIMESTAMPTZ,
    duration_seconds    NUMERIC GENERATED ALWAYS AS (
                            EXTRACT(EPOCH FROM (finished_at - started_at))
                        ) STORED,

    -- status: running | completed | failed
    status              TEXT NOT NULL DEFAULT 'running'
                            CHECK (status IN ('running', 'completed', 'failed')),

    error_message       TEXT
);

CREATE INDEX IF NOT EXISTS idx_ingestion_runs_raw_file ON ingestion_runs (raw_file_id);
CREATE INDEX IF NOT EXISTS idx_ingestion_runs_status   ON ingestion_runs (status);
