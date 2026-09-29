-- 032_create_rejection_counts.sql
-- Rejections are counted per run, source and reason instead of being kept as
-- one row each.  rejected_rows becomes a bounded sample for diagnosis; the
-- complete rows can be recovered by replaying the archived source ZIP.

CREATE TABLE IF NOT EXISTS rejection_counts (
    run_id              INT         NOT NULL REFERENCES ingestion_runs (id),
    source_file         TEXT        NOT NULL,
    rejection_reason    TEXT        NOT NULL,
    row_count           BIGINT      NOT NULL,
    PRIMARY KEY (run_id, source_file, rejection_reason),
    CONSTRAINT chk_rejection_counts_positive CHECK (row_count > 0)
);

COMMENT ON TABLE rejection_counts IS
    'Rejected rows per ingestion run, source and reason; the complete count even when rejected_rows keeps only a sample.';
COMMENT ON COLUMN rejection_counts.source_file IS
    'Same value as rejected_rows.source_file: a staged CSV or a processing step such as clean_productos.';
COMMENT ON COLUMN rejection_counts.row_count IS
    'Every row rejected for this reason in this run, sampled or not.';
COMMENT ON COLUMN rejection_counts.rejection_reason IS
    'Stable reason code, such as producto_not_found; matches rejected_rows.rejection_code.';

-- Samples carry the code they are counted under; rejection_reason keeps the
-- detailed message with the offending value.  Rows from earlier runs have no code.
ALTER TABLE rejected_rows ADD COLUMN IF NOT EXISTS rejection_code TEXT;

COMMENT ON COLUMN rejected_rows.rejection_code IS
    'Stable reason code counted in rejection_counts; NULL for rows stored before sampling.';
COMMENT ON COLUMN rejected_rows.rejection_reason IS
    'Detailed reason for this row, including the offending value.';
