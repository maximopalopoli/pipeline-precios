"""Persist and validate product-row reconciliation for one ingestion run."""

from __future__ import annotations

from dataclasses import dataclass

import structlog


log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class ProductReconciliation:
    """Account for valid product rows from cleaning through publication."""

    rows_valid: int
    rows_rejected: int
    rows_without_keys: int
    rows_fact_pending: int
    rows_fact_published: int

    def __post_init__(self) -> None:
        values = self.__dict__
        invalid = {name: value for name, value in values.items() if value < 0}
        if invalid:
            raise ValueError(f"Reconciliation counts must be nonnegative: {invalid}")
        if self.rows_valid != self.rows_without_keys + self.rows_fact_pending:
            raise ValueError(
                "Valid product rows must equal rows without keys plus pending facts"
            )
        if self.rows_fact_published != self.rows_fact_pending:
            raise ValueError("Published facts must equal pending facts for one run")


def record_product_reconciliation(
    conn,
    run_id: int,
    counts: ProductReconciliation,
) -> None:
    """Store a completed product reconciliation for *run_id*.

    The caller controls the transaction, so this can be committed atomically
    with publication or rolled back together with a failed ingestion.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE ingestion_runs
               SET rows_productos_valid = %s,
                   rows_productos_rejected = %s,
                   rows_productos_without_keys = %s,
                   rows_fact_pending = %s,
                   rows_fact_published = %s
             WHERE id = %s
            """,
            (
                counts.rows_valid,
                counts.rows_rejected,
                counts.rows_without_keys,
                counts.rows_fact_pending,
                counts.rows_fact_published,
                run_id,
            ),
        )
        if cur.rowcount != 1:
            raise ValueError(f"Ingestion run {run_id} does not exist.")
    log.info("ingestion.reconciliation_recorded", run_id=run_id, **counts.__dict__)
