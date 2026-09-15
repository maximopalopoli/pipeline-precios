-- Immutable audit trail for destructive storage-retention operations.

CREATE TABLE IF NOT EXISTS retention_events (
    id          BIGSERIAL   PRIMARY KEY,
    kind        TEXT        NOT NULL CHECK (kind IN ('fact_partitions', 'staging_runs')),
    cutoff      DATE        NOT NULL,
    targets     JSONB       NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_retention_events_created_at
    ON retention_events (created_at DESC);
