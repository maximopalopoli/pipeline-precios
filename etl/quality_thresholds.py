"""Configurable limits that stop a bad source file from being published.

Reconciliation proves every product row is accounted for; these limits decide
whether the accounted losses are acceptable.  A full run whose rejected rows,
rows without keys or rows of stores missing from ``sucursales.csv`` exceed
their share fails before publication, so the previous snapshot stays visible.
Partial development runs are not checked: their bounds select rows on purpose.

Defaults leave wide headroom over the 2026-03-23 and 03-24 AMBA replays:
0.03% rejected, no rows without keys and 0.75% of in-scope rows from unlisted
stores.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Mapping

import structlog

from etl.reconciliation import ProductReconciliation

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class QualityThresholds:
    """Limits for one ingestion run; shares are fractions between 0 and 1."""

    min_product_rows: int = 100
    max_row_count_drift: float = 0.20
    max_rejected_share: float = 0.01
    max_without_keys_share: float = 0.01
    max_store_unlisted_share: float = 0.05


_ENVIRONMENT = {
    "min_product_rows": "SEPA_MIN_PRODUCT_ROWS",
    "max_row_count_drift": "SEPA_MAX_ROW_COUNT_DRIFT",
    "max_rejected_share": "SEPA_MAX_REJECTED_SHARE",
    "max_without_keys_share": "SEPA_MAX_WITHOUT_KEYS_SHARE",
    "max_store_unlisted_share": "SEPA_MAX_STORE_UNLISTED_SHARE",
}


class RejectionThresholdError(ValueError):
    """A full run lost more rows than its configured limits allow."""


def thresholds_from_environment(env: Mapping[str, str] | None = None) -> QualityThresholds:
    """Read the limits, keeping the default of every unset variable."""
    values = os.environ if env is None else env
    configured: dict[str, int | float] = {}
    for field, variable in _ENVIRONMENT.items():
        raw = values.get(variable, "").strip()
        if not raw:
            continue
        if field == "min_product_rows":
            try:
                value: int | float = int(raw)
            except ValueError:
                raise ValueError(f"{variable} must be a whole number") from None
            if value < 0:
                raise ValueError(f"{variable} must be zero or positive")
        else:
            try:
                value = float(raw)
            except ValueError:
                raise ValueError(f"{variable} must be a number") from None
            if not 0 <= value <= 1:
                raise ValueError(f"{variable} must be between 0 and 1")
        configured[field] = value
    return QualityThresholds(**configured)


def _share(part: int, whole: int) -> float:
    return part / whole if whole else 0.0


def check_rejection_thresholds(
    conn,
    run_id: int,
    counts: ProductReconciliation,
    thresholds: QualityThresholds | None = None,
) -> dict[str, float]:
    """Raise ``RejectionThresholdError`` when a full run lost too many rows.

    Returns the measured shares.  Rows of stores missing from the day's
    ``sucursales.csv`` are measured against the rows inside the regional
    scope (unlisted + raw).
    """
    if counts.is_partial:
        return {}
    limits = thresholds_from_environment() if thresholds is None else thresholds
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT rows_productos_raw, rows_productos_store_unlisted
            FROM ingestion_runs WHERE id = %s
            """,
            (run_id,),
        )
        row = cur.fetchone()
    if row is None:
        raise ValueError(f"Ingestion run {run_id} does not exist.")
    rows_raw = row[0] if row[0] is not None else counts.rows_valid + counts.rows_rejected
    rows_unlisted = row[1] or 0

    shares = {
        "rejected": _share(counts.rows_rejected, rows_raw),
        "without_keys": _share(counts.rows_without_keys, counts.rows_valid),
        "store_unlisted": _share(rows_unlisted, rows_unlisted + rows_raw),
    }
    limits_by_share = {
        "rejected": limits.max_rejected_share,
        "without_keys": limits.max_without_keys_share,
        "store_unlisted": limits.max_store_unlisted_share,
    }
    exceeded = {
        name: {"share": round(share, 6), "limit": limits_by_share[name]}
        for name, share in shares.items()
        if share > limits_by_share[name]
    }
    log.info(
        "quality.rejection_thresholds_checked",
        run_id=run_id,
        rows_raw=rows_raw,
        rows_rejected=counts.rows_rejected,
        rows_without_keys=counts.rows_without_keys,
        rows_store_unlisted=rows_unlisted,
        **{f"{name}_share": round(share, 6) for name, share in shares.items()},
        **{f"{name}_limit": limit for name, limit in limits_by_share.items()},
        passed=not exceeded,
    )
    if exceeded:
        raise RejectionThresholdError(
            f"Run {run_id} exceeds rejection limits: {exceeded}"
        )
    return shares
