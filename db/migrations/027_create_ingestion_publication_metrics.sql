-- Break down the atomic publication phase so full replay bottlenecks can be
-- measured without weakening the pending-fact visibility boundary.

CREATE TABLE IF NOT EXISTS ingestion_publication_metrics (
    run_id                    INT PRIMARY KEY REFERENCES ingestion_runs (id) ON DELETE CASCADE,
    delete_existing_seconds   NUMERIC NOT NULL CHECK (delete_existing_seconds >= 0),
    insert_facts_seconds      NUMERIC NOT NULL CHECK (insert_facts_seconds >= 0),
    aggregate_delete_seconds  NUMERIC NOT NULL CHECK (aggregate_delete_seconds >= 0),
    aggregate_insert_seconds  NUMERIC NOT NULL CHECK (aggregate_insert_seconds >= 0),
    cleanup_seconds           NUMERIC NOT NULL CHECK (cleanup_seconds >= 0),
    recorded_at               TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE ingestion_publication_metrics IS
    'Durable timings for the substeps of a full atomic pending-fact publication.';
