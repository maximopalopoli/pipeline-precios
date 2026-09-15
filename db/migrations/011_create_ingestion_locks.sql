-- A durable, cross-entrypoint mutex for one published SEPA date.
--
-- Airflow tasks use distinct database connections, so session advisory locks
-- cannot cover the whole ingestion lifecycle.  The token makes releasing a
-- lock safe: only the owner that acquired it can remove it.

CREATE TABLE IF NOT EXISTS ingestion_locks (
    fecha_datos DATE        PRIMARY KEY,
    raw_file_id INT         NOT NULL REFERENCES raw_files (id),
    owner       TEXT        NOT NULL,
    token       UUID        NOT NULL UNIQUE,
    acquired_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ingestion_locks_raw_file
    ON ingestion_locks (raw_file_id);
