"""Explicit bounds for a local, partial SEPA ingestion.

This module defines and validates the bounded-ingestion contract.  Staging
applies the bounds and publication skips a partial run, so a policy is enabled
only by an explicit setting: an unset or blank value must never silently turn
a full ingestion into a partial one.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from collections.abc import Iterable
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


def with_cli_overrides(
    policy: DevelopmentIngestionPolicy,
    *,
    chain_keys: Iterable[str] | None = None,
    max_product_rows: int | None = None,
) -> DevelopmentIngestionPolicy:
    """Apply explicit CLI bounds over an environment-derived policy.

    Supplying a CLI flag overrides only that bound. This makes ad-hoc local
    runs reproducible while retaining the same validation contract as the
    environment configuration used by scheduled runs.
    """
    selected_chain_keys = (
        policy.chain_keys
        if chain_keys is None
        else _parse_chain_keys(",".join(chain_keys), "--dev-chain-key")
    )
    if max_product_rows is not None and max_product_rows <= 0:
        raise ValueError("--dev-max-product-rows must be a positive whole number.")
    return DevelopmentIngestionPolicy(
        chain_keys=selected_chain_keys,
        max_product_rows=(
            policy.max_product_rows if max_product_rows is None else max_product_rows
        ),
    )


def _read_chain_keys(values: Mapping[str, str]) -> tuple[tuple[int, int], ...]:
    raw_value = values.get(_CHAIN_KEYS_ENV, "").strip()
    if not raw_value:
        return ()

    return _parse_chain_keys(raw_value, _CHAIN_KEYS_ENV)


def _parse_chain_keys(
    raw_value: str, setting_name: str
) -> tuple[tuple[int, int], ...]:

    chain_keys: set[tuple[int, int]] = set()
    for raw_key in raw_value.split(","):
        parts = raw_key.strip().split(":")
        if len(parts) != 2:
            raise ValueError(
                f"{setting_name} entries must use id_comercio:id_bandera."
            )
        try:
            key = (int(parts[0]), int(parts[1]))
        except ValueError as exc:
            raise ValueError(
                f"{setting_name} entries must use positive integer identifiers."
            ) from exc
        if key[0] <= 0 or key[1] <= 0:
            raise ValueError(
                f"{setting_name} entries must use positive integer identifiers."
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
