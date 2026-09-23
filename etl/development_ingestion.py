"""Explicit bounds for a local, partial SEPA ingestion.

This module only defines and validates the bounded-ingestion contract.  The
staging and publication steps consume it in subsequent changes, so parsing a
policy alone must never silently turn a full ingestion into a partial one.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping


_CHAIN_KEYS_ENV = "SEPA_DEV_CHAIN_KEYS"
_MAX_PRODUCT_ROWS_ENV = "SEPA_DEV_MAX_PRODUCT_ROWS"


@dataclass(frozen=True)
class DevelopmentIngestionPolicy:
    """Opt-in deterministic bounds for an ingestion used only in development.

    A chain is identified by the source's natural key
    ``id_comercio:id_bandera``.  Keeping both fields prevents accidentally
    selecting a different chain when an identifier is only locally unique.
    """

    chain_keys: tuple[tuple[int, int], ...] = ()
    max_product_rows: int | None = None

    @property
    def enabled(self) -> bool:
        """Whether this policy requests a partial ingestion."""
        return bool(self.chain_keys) or self.max_product_rows is not None


def from_environment(
    env: Mapping[str, str] | None = None,
) -> DevelopmentIngestionPolicy:
    """Read explicit local-development ingestion bounds from the environment.

    ``SEPA_DEV_CHAIN_KEYS`` accepts a comma-separated list of
    ``id_comercio:id_bandera`` pairs. ``SEPA_DEV_MAX_PRODUCT_ROWS`` accepts a
    positive whole number. Unset or blank values leave each bound disabled;
    malformed values fail fast rather than broadening a requested local run.
    """
    values = os.environ if env is None else env
    return DevelopmentIngestionPolicy(
        chain_keys=_read_chain_keys(values),
        max_product_rows=_read_positive_integer(values, _MAX_PRODUCT_ROWS_ENV),
    )


def _read_chain_keys(values: Mapping[str, str]) -> tuple[tuple[int, int], ...]:
    raw_value = values.get(_CHAIN_KEYS_ENV, "").strip()
    if not raw_value:
        return ()

    chain_keys: set[tuple[int, int]] = set()
    for raw_key in raw_value.split(","):
        parts = raw_key.strip().split(":")
        if len(parts) != 2:
            raise ValueError(
                f"{_CHAIN_KEYS_ENV} entries must use id_comercio:id_bandera."
            )
        try:
            key = (int(parts[0]), int(parts[1]))
        except ValueError as exc:
            raise ValueError(
                f"{_CHAIN_KEYS_ENV} entries must use positive integer identifiers."
            ) from exc
        if key[0] <= 0 or key[1] <= 0:
            raise ValueError(
                f"{_CHAIN_KEYS_ENV} entries must use positive integer identifiers."
            )
        chain_keys.add(key)
    return tuple(sorted(chain_keys))


def _read_positive_integer(values: Mapping[str, str], name: str) -> int | None:
    raw_value = values.get(name, "").strip()
    if not raw_value:
        return None
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive whole number.") from exc
    if value <= 0:
        raise ValueError(f"{name} must be a positive whole number.")
    return value
