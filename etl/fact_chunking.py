"""Configuration for bounded product-fact processing batches."""

from __future__ import annotations

import os
from collections.abc import Mapping

from etl.transform.fact_builder import PRODUTO_CHUNK_SIZE

ENVIRONMENT_VARIABLE = "SEPA_FACT_CHUNK_SIZE"


def from_environment(env: Mapping[str, str] | None = None) -> int:
    """Return the configured positive batch size, or the stable default.

    This setting changes only work partitioning: it cannot change the fact
    grain, publication boundary, or whether a run is partial.
    """
    values = os.environ if env is None else env
    raw = values.get(ENVIRONMENT_VARIABLE)
    if raw is None or not raw.strip():
        return PRODUTO_CHUNK_SIZE
    try:
        chunk_size = int(raw)
    except ValueError as exc:
        raise ValueError(f"{ENVIRONMENT_VARIABLE} must be a positive integer") from exc
    if chunk_size <= 0:
        raise ValueError(f"{ENVIRONMENT_VARIABLE} must be a positive integer")
    return chunk_size


def with_cli_override(*, chunk_size: int | None, env: Mapping[str, str] | None = None) -> int:
    """Prefer an explicit CLI size over the environment configuration."""
    if chunk_size is None:
        return from_environment(env)
    if chunk_size <= 0:
        raise ValueError("fact chunk size must be a positive integer")
    return chunk_size
