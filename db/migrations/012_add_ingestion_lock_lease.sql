-- A process can terminate before its failure handler releases a durable lock.
-- Expiring leases make that date available for a later retry without allowing
-- an active execution to be replaced.

ALTER TABLE ingestion_locks
    ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ;

UPDATE ingestion_locks
SET expires_at = acquired_at + INTERVAL '6 hours'
WHERE expires_at IS NULL;

ALTER TABLE ingestion_locks
    ALTER COLUMN expires_at SET NOT NULL;

CREATE INDEX IF NOT EXISTS idx_ingestion_locks_expires_at
    ON ingestion_locks (expires_at);
