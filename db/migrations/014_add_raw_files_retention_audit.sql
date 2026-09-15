ALTER TABLE retention_events
    DROP CONSTRAINT retention_events_kind_check;

ALTER TABLE retention_events
    ADD CONSTRAINT retention_events_kind_check
    CHECK (kind IN ('fact_partitions', 'staging_runs', 'raw_files'));
