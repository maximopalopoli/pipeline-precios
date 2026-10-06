"""Unit tests for etl/log_output.py."""

from datetime import datetime, timedelta
import io

import pytest
import structlog

from etl.log_output import (
    FullLogWriter,
    MilestoneRenderer,
    configure_logging,
    default_log_path,
)


class _Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 6, 21, 30, 0)

    def __call__(self):
        return self.now


def _render(renderer, event, level="info", **fields):
    try:
        return renderer(None, level, {"event": event, "level": level, **fields})
    except structlog.DropEvent:
        return None


@pytest.fixture
def renderer():
    return MilestoneRenderer(clock=_Clock())


@pytest.fixture(autouse=True)
def _reset_structlog():
    yield
    structlog.reset_defaults()


def test_chunk_and_bookkeeping_events_are_dropped(renderer):
    assert _render(renderer, "pipeline.stage.start", stage="staging") is None
    assert _render(renderer, "pipeline.stage.done", stage="pending_fact_write") is None
    assert _render(renderer, "pipeline.chunk.throughput", rows=300_000) is None
    assert _render(renderer, "fact_builder.start", rows=300_000) is None
    assert _render(renderer, "pipeline.ingestion_lock.released") is None


def test_header_names_date_scope_source_and_full_log():
    log_path = default_log_path(datetime(2026, 10, 6, 21, 30))
    renderer = MilestoneRenderer(log_path=log_path, clock=_Clock())
    line = _render(
        renderer, "pipeline.started", fecha="2026-10-06", source="remote",
        regional_scope=None, is_partial=False,
    )
    assert line.startswith("21:30:00  Ingest 2026-10-06 · scope national · source remote")
    assert "Full log: logs/ingest-20261006-213000.log" in line


def test_staging_line_counts_rows_in_and_out_of_scope(renderer):
    line = _render(
        renderer, "pipeline.stage.done", stage="staging", elapsed_seconds=118.2,
        rows_productos=7_652_473, rows_productos_out_of_scope=6_987_107,
        rows_productos_store_unlisted=58_306, inner_zips_invalid=0,
    )
    assert line == (
        "21:30:00  ✓ staging      7.65 M product rows · 6.99 M out of scope"
        " · 58 k of unlisted stores · 1 min 58 s"
    )


def test_fact_progress_every_twenty_percent_with_time_left():
    clock = _Clock()
    renderer = MilestoneRenderer(clock=clock)
    _render(renderer, "pipeline.stage.done", stage="staging", rows_productos=1_000_000)
    _render(renderer, "pipeline.stage.start", stage="fact_load")
    lines = []
    for _ in range(10):
        clock.now += timedelta(seconds=30)
        lines.append(_render(
            renderer, "pipeline.stage.done", stage="fact_chunk_preparation",
            rows_valid=99_000, rows_rejected=1_000,
        ))
    shown = [line for line in lines if line is not None]
    assert [line.split("·")[0].split()[-1] for line in shown] == ["20%", "40%", "60%", "80%"]
    assert shown[0].endswith("200 k of 1.00 M rows · ~4 min left")


def test_failed_loss_limit_names_its_limit(renderer):
    line = _render(
        renderer, "quality.rejection_thresholds_checked", passed=False,
        rejected_share=0.000083, rejected_limit=0.01,
        without_keys_share=0.031171, without_keys_limit=0.01,
        store_unlisted_share=0.0, store_unlisted_limit=0.05,
    )
    assert line == (
        "21:30:00  ✗ loss limits  rejected 0.01% · without store or product 3.12%"
        " (limit 1.00%) · unlisted stores 0.00%"
    )


def test_quality_line_names_failed_warning_checks(renderer):
    assert _render(
        renderer, "quality.check.result", level="warning", check="tipo_sucursal", passed=False
    ) is None
    line = _render(renderer, "pipeline.stage.done", stage="quality", elapsed_seconds=31)
    assert line == "21:30:00  ✓ quality      passed with 1 warning: tipo_sucursal · 31 s"


def test_failure_is_reported_once(renderer):
    assert _render(renderer, "pipeline.run.failed", level="error") is None
    assert _render(renderer, "pipeline.alert.failure", level="error", message="boom") is None
    assert _render(
        renderer, "pipeline.stage.failed", stage="publication",
        elapsed_seconds=0.414, error_type="RejectionThresholdError",
    ) == "21:30:00  ✗ publication  failed after 0.4 s (RejectionThresholdError)"
    assert _render(renderer, "pipeline.fatal_error", level="error", error="boom") == (
        "21:30:00  Failed: boom"
    )


def test_unsummarized_warnings_are_shown(renderer):
    assert _render(renderer, "cleaner.rejected_rows", level="warning", count=3) is None
    line = _render(
        renderer, "staging.inner_zip_invalid", level="warning", run_id=1,
        inner_zip="sepa_10.zip", error_type="BadZipFile",
    )
    assert line == "21:30:00  ! staging.inner_zip_invalid inner_zip=sepa_10.zip error_type=BadZipFile"


def test_partial_publication_says_nothing_was_published(renderer):
    line = _render(
        renderer, "pipeline.stage.done", stage="publication", elapsed_seconds=1,
        is_partial=True, rows_fact_pending=5_000,
    )
    assert "partial run: 5,000 prices pending, none published" in line


def test_full_log_writer_keeps_every_event():
    stream = io.StringIO()
    writer = FullLogWriter(stream)
    event = {"event": "pipeline.chunk.throughput", "level": "info", "rows": 300_000}
    assert writer(None, "info", event) is event
    assert "pipeline.chunk.throughput" in stream.getvalue()
    assert "rows=300000" in stream.getvalue()


def test_configure_logging_prints_milestones_and_files_everything(tmp_path, capsys):
    log_path = tmp_path / "logs" / "ingest.log"
    configure_logging(verbose=False, log_path=log_path)
    log = structlog.get_logger("test")
    log.info("pipeline.chunk.throughput", rows=300_000)
    log.info("pipeline.completed", run_id=3, elapsed_total=710.0, peak_rss_bytes=1_390_000_000)

    printed = capsys.readouterr().out
    assert "pipeline.chunk.throughput" not in printed
    assert "Completed in 11 min 50 s · run 3 · peak memory 1.29 GB" in printed
    written = log_path.read_text()
    assert "pipeline.chunk.throughput" in written
    assert "pipeline.completed" in written


def test_verbose_prints_every_event(capsys):
    configure_logging(verbose=True, log_path=None)
    structlog.get_logger("test").info("pipeline.chunk.throughput", rows=300_000)
    assert "pipeline.chunk.throughput" in capsys.readouterr().out
