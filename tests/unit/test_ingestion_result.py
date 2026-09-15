"""Unit tests for the public non-error ingestion result."""

from etl.ingestion_run import IngestionOutcome, IngestionResult


def test_completed_result_keeps_the_loaded_row_count():
    result = IngestionResult.completed(42)

    assert result.outcome is IngestionOutcome.COMPLETED
    assert result.rows_loaded == 42


def test_skipped_result_has_no_loaded_rows():
    result = IngestionResult.skipped()

    assert result.outcome is IngestionOutcome.SKIPPED
    assert result.rows_loaded == 0
