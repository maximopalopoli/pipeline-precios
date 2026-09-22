"""Small, shared primitives for recording ingestion-stage performance."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any


@dataclass
class StageTimer:
    """Measure one named stage and emit consistent structured log events.

    Entrypoints own the work between :meth:`start` and :meth:`finish`; this
    helper only records monotonic elapsed time.  Optional fields passed to
    ``finish`` keep volume and outcome metrics on the same event as the timing.
    """

    logger: Any
    stage: str
    clock: Callable[[], float] = perf_counter
    _started_at: float | None = field(default=None, init=False, repr=False)

    def start(self, **fields: object) -> None:
        """Start the measurement and log the beginning of its stage."""
        if self._started_at is not None:
            raise RuntimeError(f"Stage {self.stage!r} is already running.")
        self._started_at = self.clock()
        self.logger.info("pipeline.stage.start", stage=self.stage, **fields)

    def finish(self, **fields: object) -> float:
        """Log completion and return elapsed monotonic seconds."""
        if self._started_at is None:
            raise RuntimeError(f"Stage {self.stage!r} has not started.")
        elapsed_seconds = self.clock() - self._started_at
        self._started_at = None
        self.logger.info(
            "pipeline.stage.done",
            stage=self.stage,
            elapsed_seconds=round(elapsed_seconds, 3),
            **fields,
        )
        return elapsed_seconds
