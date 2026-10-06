"""CLI log output: a short milestone view by default, every event when verbose.

The ETL emits many structured events (several per product chunk).  They stay
the source of truth: every event is always written to a full log file, and
``--verbose`` prints them to the terminal as before.  By default the terminal
shows one line per milestone (stages, fact progress, loss limits, outcome) and
any warning or error that no milestone already summarizes.

Airflow does not call :func:`configure_logging`, so its task logs keep every
event.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

import structlog

LOG_DIR = Path("logs")

_STAGE_LABELS = {
    "download": "download",
    "staging": "staging",
    "quality": "quality",
    "dimensions": "dimensions",
    "fact_load": "facts",
    "publication": "publication",
}

# Warnings and errors that a milestone line already reports, or that repeat
# the final failure message.
_SUMMARIZED_EVENTS = {
    "cleaner.rejected_rows",
    "fact_builder.rejected_rows",
    "fact_builder.sucursal_missing_comercio",
    "pipeline.run.failed",
    "pipeline.alert.failure",
    "pipeline.aborted",
    "quality.failed",
    "quality.report.summary",
}

_HIDDEN_FIELDS = {"event", "level", "timestamp", "run_id", "exc_info"}

_PROGRESS_STEP = 20


def configure_logging(*, verbose: bool, log_path: Path | None) -> None:
    """Print milestones (or every event when *verbose*); copy all to *log_path*."""
    processors: list[Any] = [
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
    ]
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        processors.append(FullLogWriter(log_path.open("a", encoding="utf-8")))
    if verbose:
        processors.append(structlog.dev.ConsoleRenderer())
    else:
        processors.append(MilestoneRenderer(log_path=log_path))
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.BoundLogger,
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )


def default_log_path(now: datetime | None = None) -> Path:
    """Return a new file name for one CLI run's full log."""
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    return LOG_DIR / f"ingest-{stamp}.log"


class FullLogWriter:
    """Write every event, as the verbose console shows it, to a file."""

    def __init__(self, stream: TextIO) -> None:
        self._stream = stream
        self._renderer = structlog.dev.ConsoleRenderer(colors=False)

    def __call__(self, logger: Any, method_name: str, event_dict: dict) -> dict:
        self._stream.write(self._renderer(logger, method_name, dict(event_dict)) + "\n")
        self._stream.flush()
        return event_dict


class MilestoneRenderer:
    """Render milestone events as short lines and drop the rest.

    It keeps a little state across events: the staged product rows, to show
    fact progress, and the quality warnings, to name them on the quality line.
    """

    def __init__(
        self,
        *,
        log_path: Path | None = None,
        clock: Any = datetime.now,
    ) -> None:
        self._log_path = log_path
        self._clock = clock
        self._rows_staged = 0
        self._rows_processed = 0
        self._next_progress = _PROGRESS_STEP
        self._fact_started_at: datetime | None = None
        self._quality_warnings: list[str] = []

    def __call__(self, logger: Any, method_name: str, event_dict: dict) -> str:
        text = self._render(event_dict)
        if text is None:
            raise structlog.DropEvent
        return f"{self._clock():%H:%M:%S}  {text}"

    def _render(self, e: dict) -> str | None:
        event = e.get("event")
        level = e.get("level", "info")

        if event == "pipeline.started":
            return self._started(e)
        if event in ("pipeline.stage.start", "pipeline.stage.done", "pipeline.stage.failed"):
            return self._stage(event, e)
        if event == "quality.check.result" and level == "warning":
            self._quality_warnings.append(str(e.get("check")))
            return None
        if event == "quality.rejection_thresholds_checked":
            return self._loss_limits(e)
        if event == "pipeline.reprocessing_failed_run":
            return "↻ retrying a failed run of this file"
        if event == "pipeline.skipped":
            return f"Skipped: {_SKIP_REASONS.get(e.get('reason'), e.get('reason'))}"
        if event == "pipeline.completed":
            return (
                f"Completed in {_duration(e.get('elapsed_total'))}"
                f" · run {e.get('run_id')}"
                f" · peak memory {_bytes(e.get('peak_rss_bytes'))}"
            )
        if event == "pipeline.raw_archive.completed":
            archived = len(e.get("archived_file_ids") or ())
            return _line("✓", "archive", f"{archived} source ZIP archived") if archived else None
        if event == "pipeline.fact_retention.completed":
            return _line("✓", "retention", f"dropped {len(e.get('partitions') or ())} old price days")
        if event == "pipeline.staging_retention.completed":
            return _line("✓", "retention", f"dropped the staging of {len(e.get('run_ids') or ())} runs")
        if event == "pipeline.raw_retention.completed":
            return _line("✓", "retention", f"deleted {len(e.get('deleted_file_ids') or ())} source ZIPs")
        if event == "pipeline.fatal_error":
            return f"Failed: {e.get('error')}"
        if event in _SUMMARIZED_EVENTS or level not in ("warning", "error", "critical"):
            return None
        mark = "!" if level == "warning" else "✗"
        return f"{mark} {event} {_fields(e)}".rstrip()

    def _started(self, e: dict) -> str:
        parts = [
            f"Ingest {e.get('fecha')}",
            f"scope {e.get('regional_scope') or 'national'}",
            f"source {e.get('source')}",
        ]
        if e.get("is_partial"):
            parts.append("partial development run (publishes nothing)")
        header = " · ".join(parts)
        if self._log_path is not None:
            header += f"\n          Full log: {self._log_path}"
        return header

    def _stage(self, event: str, e: dict) -> str | None:
        stage = e.get("stage")
        if stage == "fact_chunk_preparation" and event == "pipeline.stage.done":
            return self._fact_progress(e)
        label = _STAGE_LABELS.get(stage)
        if label is None:
            return None
        if event == "pipeline.stage.start":
            if stage == "fact_load":
                self._fact_started_at = self._clock()
            return None
        elapsed = _duration(e.get("elapsed_seconds"))
        if event == "pipeline.stage.failed":
            cause = e.get("error_type") or e.get("reason") or "error"
            return _line("✗", label, f"failed after {elapsed} ({cause})")
        return _line("✓", label, f"{self._stage_summary(stage, e)}", elapsed)

    def _stage_summary(self, stage: str, e: dict) -> str:
        if stage == "download":
            return f"{e.get('filename')} · {_bytes(e.get('size_bytes'))}"
        if stage == "staging":
            self._rows_staged = int(e.get("rows_productos") or 0)
            parts = [f"{_count(self._rows_staged)} product rows"]
            if e.get("rows_productos_out_of_scope"):
                parts.append(f"{_count(e['rows_productos_out_of_scope'])} out of scope")
            if e.get("rows_productos_store_unlisted"):
                parts.append(f"{_count(e['rows_productos_store_unlisted'])} of unlisted stores")
            if e.get("inner_zips_invalid"):
                parts.append(f"{e['inner_zips_invalid']} invalid inner ZIPs")
            return " · ".join(parts)
        if stage == "quality":
            if not self._quality_warnings:
                return "all checks passed"
            names = ", ".join(self._quality_warnings)
            noun = "warning" if len(self._quality_warnings) == 1 else "warnings"
            return f"passed with {len(self._quality_warnings)} {noun}: {names}"
        if stage == "dimensions":
            text = (
                f"{_count(e.get('rows_sucursales'))} stores"
                f" and {_count(e.get('rows_productos'))} products written"
            )
            if e.get("rows_rejected"):
                text += f" · {_count(e['rows_rejected'])} chain or store rows rejected"
            return text
        if stage == "fact_load":
            return (
                f"{_count(e.get('rows_fact'))} prices ready"
                f" · {_count(e.get('rows_rejected'))} rejected"
                f" · {_count(e.get('rows_without_keys'))} without store or product"
                f" · {_count(e.get('rows_deduplicated'))} duplicates"
            )
        if e.get("is_partial"):
            return f"partial run: {_count(e.get('rows_fact_pending'))} prices pending, none published"
        text = f"{_count(e.get('rows_fact_published'))} prices published"
        if e.get("rows_carried_forward"):
            text += f" · {_count(e['rows_carried_forward'])} carried forward"
        return text

    def _fact_progress(self, e: dict) -> str | None:
        self._rows_processed += int(e.get("rows_valid") or 0) + int(e.get("rows_rejected") or 0)
        if not self._rows_staged:
            return None
        percent = 100 * self._rows_processed / self._rows_staged
        if percent < self._next_progress or percent >= 100:
            return None
        while self._next_progress <= percent:
            self._next_progress += _PROGRESS_STEP
        text = (
            f"{int(percent)}% · {_count(self._rows_processed)}"
            f" of {_count(self._rows_staged)} rows"
        )
        if self._fact_started_at is not None and self._rows_processed:
            spent = (self._clock() - self._fact_started_at).total_seconds()
            left = spent * (self._rows_staged - self._rows_processed) / self._rows_processed
            text += f" · ~{_duration(left)} left"
        return _line(" ", "facts", text)

    def _loss_limits(self, e: dict) -> str:
        parts = []
        for name, label in (
            ("rejected", "rejected"),
            ("without_keys", "without store or product"),
            ("store_unlisted", "unlisted stores"),
        ):
            share = e.get(f"{name}_share")
            limit = e.get(f"{name}_limit")
            text = f"{label} {_percent(share)}"
            if limit is not None and share is not None and share > limit:
                text += f" (limit {_percent(limit)})"
            parts.append(text)
        mark = "✓" if e.get("passed") else "✗"
        return _line(mark, "loss limits", " · ".join(parts))


_SKIP_REASONS = {
    "duplicate_sha256": "this file was already ingested",
    "completed_while_waiting_for_ingestion_lock": "another run ingested this file meanwhile",
}


def _line(mark: str, label: str, text: str, elapsed: str | None = None) -> str:
    line = f"{mark} {label:<12} {text}"
    return f"{line} · {elapsed}" if elapsed else line


def _fields(e: dict) -> str:
    return " ".join(f"{k}={v}" for k, v in e.items() if k not in _HIDDEN_FIELDS)


def _count(value: Any) -> str:
    if value is None:
        return "?"
    n = int(value)
    if abs(n) >= 1_000_000:
        return f"{n / 1_000_000:.2f} M"
    if abs(n) >= 10_000:
        return f"{n / 1_000:.0f} k"
    return f"{n:,}"


def _duration(seconds: Any) -> str:
    if seconds is None:
        return "?"
    s = float(seconds)
    if s < 10:
        return f"{s:.1f} s"
    if s < 60:
        return f"{s:.0f} s"
    minutes, rest = divmod(round(s), 60)
    return f"{minutes} min {rest} s" if rest else f"{minutes} min"


def _bytes(value: Any) -> str:
    if value is None:
        return "?"
    n = float(value)
    if n >= 1024**3:
        return f"{n / 1024**3:.2f} GB"
    return f"{n / 1024**2:.0f} MB"


def _percent(share: Any) -> str:
    if share is None:
        return "?"
    return f"{100 * float(share):.2f}%"
