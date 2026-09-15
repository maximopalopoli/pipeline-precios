"""Explicit, opt-in storage retention settings for local ingestion."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Mapping


_FACT_RETENTION_DAYS_ENV = "SEPA_FACT_RETENTION_DAYS"


@dataclass(frozen=True)
class RetentionPolicy:
    """Controls how long detailed price facts remain in PostgreSQL.

    Retention is disabled unless explicitly configured.  This keeps a fresh
    checkout and production-like environments from deleting historical detail
    merely because an environment variable was omitted or misspelled.
    """

    fact_retention_days: int | None = None

    @property
    def fact_retention_enabled(self) -> bool:
        return self.fact_retention_days is not None

    def fact_cutoff(self, as_of: date) -> date | None:
        """Return the oldest date to retain, or ``None`` when disabled."""
        if self.fact_retention_days is None:
            return None
        return as_of - timedelta(days=self.fact_retention_days)


def from_environment(env: Mapping[str, str] | None = None) -> RetentionPolicy:
    """Read the opt-in fact retention window from the environment.

    ``SEPA_FACT_RETENTION_DAYS`` accepts a positive whole number.  An unset or
    blank value disables retention; invalid values fail fast rather than
    silently choosing a destructive default.
    """
    values = os.environ if env is None else env
    raw_value = values.get(_FACT_RETENTION_DAYS_ENV, "").strip()
    if not raw_value:
        return RetentionPolicy()

    try:
        days = int(raw_value)
    except ValueError as exc:
        raise ValueError(
            f"{_FACT_RETENTION_DAYS_ENV} must be a positive whole number."
        ) from exc
    if days <= 0:
        raise ValueError(f"{_FACT_RETENTION_DAYS_ENV} must be a positive whole number.")
    return RetentionPolicy(fact_retention_days=days)
