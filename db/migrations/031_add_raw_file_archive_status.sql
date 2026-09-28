-- 031_add_raw_file_archive_status.sql
-- The weekday SEPA URL is overwritten every week, so an archived copy of each
-- completed ZIP is the only durable history and reprocessing source.  A raw file
-- is archived only after its copy was verified against the registered SHA-256.

ALTER TABLE raw_files
    ADD COLUMN IF NOT EXISTS archive_key TEXT,
    ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'chk_raw_files_archive_complete'
          AND conrelid = 'raw_files'::regclass
    ) THEN
        ALTER TABLE raw_files
            ADD CONSTRAINT chk_raw_files_archive_complete CHECK (
                (archive_key IS NULL) = (archived_at IS NULL)
            );
    END IF;
END
$$;

COMMENT ON COLUMN raw_files.archive_key IS
    'Location of the verified archived copy, relative to the archive root (YYYY/MM/<declared date>_<sha256>.zip). The date is the one declared inside the ZIP, not raw_files.fecha_datos.';
COMMENT ON COLUMN raw_files.archived_at IS
    'When the archived copy was verified against sha256 and size_bytes; NULL while not archived.';
