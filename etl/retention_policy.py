"""Explicit, opt-in storage retention settings for local ingestion."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Mapping


_FACT_RETENTION_DAYS_ENV = "SEPA_FACT_RETENTION_DAYS"
_STAGING_RETENTION_DAYS_ENV = "SEPA_STAGING_RETENTION_DAYS"


@dataclass(frozen=True)
class RetentionPolicy:
    """Controls how long detailed price facts remain in PostgreSQL.

    Retention is disabled unless explicitly configured.  This keeps a fresh
    checkout and production-like environments from deleting historical detail
    merely because an environment variable was omitted or misspelled.
    """

    fact_retention_days: int | None = None
    staging_retention_days: int | None = None

    @property
    def fact_retention_enabled(self) -> bool:
        return self.fact_retention_days is not None

    @property
    def staging_retention_enabled(self) -> bool:
        return self.staging_retention_days is not None

    def fact_cutoff(self, as_of: date) -> date | None:
        """Return the oldest date to retain, or ``None`` when disabled."""
        if self.fact_retention_days is None:
            return None
        return as_of - timedelta(days=self.fact_retention_days)

    def staging_cutoff(self, as_of: date) -> date | None:
        """Return the oldest terminal staging run to retain, if enabled."""
        if self.staging_retention_days is None:
            return None
        return as_of - timedelta(days=self.staging_retention_days)


def from_environment(env: Mapping[str, str] | None = None) -> RetentionPolicy:
    """Read the opt-in fact retention window from the environment.

    Each ``*_RETENTION_DAYS`` setting accepts a positive whole number. An unset
    or blank value disables that retention class; invalid values fail fast
    rather than silently choosing a destructive default.
    """
    values = os.environ if env is None else env
    return RetentionPolicy(
        fact_retention_days=_read_positive_days(values, _FACT_RETENTION_DAYS_ENV),
        staging_retention_days=_read_positive_days(values, _STAGING_RETENTION_DAYS_ENV),
    )


def _read_positive_days(values: Mapping[str, str], name: str) -> int | None:
    raw_value = values.get(name, "").strip()
    if not raw_value:
        return None

    try:
        days = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive whole number.") from exc
    if days <= 0:
        raise ValueError(f"{name} must be a positive whole number.")
    return days
