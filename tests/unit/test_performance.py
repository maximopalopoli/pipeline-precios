"""Unit tests for shared ingestion performance instrumentation."""

import pytest

from etl.performance import StageTimer


class CapturingLogger:
    def __init__(self):
        self.events: list[tuple[str, dict[str, object]]] = []

    def info(self, event: str, **fields: object) -> None:
        self.events.append((event, fields))


def test_stage_timer_logs_a_consistent_start_and_completion_event():
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
