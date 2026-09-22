"""Unit tests for shared ingestion performance instrumentation."""

import etl.performance as performance
import pytest

from etl.performance import StageTimer, process_max_rss_bytes


class CapturingLogger:
    def __init__(self):
        self.events: list[tuple[str, dict[str, object]]] = []

    def info(self, event: str, **fields: object) -> None:
        self.events.append((event, fields))


def test_stage_timer_logs_a_consistent_start_and_completion_event(monkeypatch):
    monkeypatch.setattr(performance, "process_max_rss_bytes", lambda: 45_056)
    logger = CapturingLogger()
    values = iter([10.0, 11.2349])
    timer = StageTimer(logger, "staging", clock=lambda: next(values))

    timer.start(run_id=42)
    elapsed = timer.finish(rows_productos=120)

    assert elapsed == pytest.approx(1.2349)
    assert logger.events == [
        ("pipeline.stage.start", {"stage": "staging", "run_id": 42}),
        (
            "pipeline.stage.done",
            {
                "stage": "staging",
                "elapsed_seconds": 1.235,
                "peak_rss_bytes": 45_056,
                "rows_productos": 120,
            },
        ),
    ]


def test_stage_timer_rejects_invalid_lifecycle_calls():
    logger = CapturingLogger()
    timer = StageTimer(logger, "quality", clock=lambda: 1.0)

    with pytest.raises(RuntimeError, match="has not started"):
        timer.finish()

    timer.start()
    with pytest.raises(RuntimeError, match="already running"):
        timer.start()


def test_stage_timer_logs_a_failed_stage_with_its_elapsed_time(monkeypatch):
    monkeypatch.setattr(performance, "process_max_rss_bytes", lambda: 65_536)
    logger = CapturingLogger()
    values = iter([3.0, 3.5])
    timer = StageTimer(logger, "quality", clock=lambda: next(values))

    timer.start()
    timer.fail(reason="critical_quality_failure")

    assert logger.events[-1] == (
        "pipeline.stage.failed",
        {
            "stage": "quality",
            "elapsed_seconds": 0.5,
            "peak_rss_bytes": 65_536,
            "reason": "critical_quality_failure",
        },
    )


class ResourceUsage:
    def __init__(self, ru_maxrss: int):
        self.ru_maxrss = ru_maxrss


def test_process_max_rss_converts_linux_kib_to_bytes():
    assert process_max_rss_bytes(
        getrusage=lambda: ResourceUsage(12_345), platform="linux"
    ) == 12_345 * 1024


def test_process_max_rss_keeps_macos_bytes_unchanged():
    assert process_max_rss_bytes(
        getrusage=lambda: ResourceUsage(12_345), platform="darwin"
    ) == 12_345
