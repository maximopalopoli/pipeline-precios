"""Persist and validate product-row reconciliation for one ingestion run."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json

import structlog

from etl.rejection_counts import record_rejections


log = structlog.get_logger(__name__)

# Sources of product rejections in rejection_counts; each must reconcile with
# its counter in ProductReconciliation.
CLEANING_SOURCE = "clean_productos"
KEY_RESOLUTION_SOURCE = "fact_key_resolution"


@dataclass(frozen=True)
class ProductReconciliation:
    """Account for valid product rows from cleaning through publication."""

    rows_valid: int
    rows_rejected: int
    rows_without_keys: int
    rows_deduplicated: int
    rows_fact_pending: int
    rows_fact_published: int
    is_partial: bool = False

    def __post_init__(self) -> None:
        values = self.__dict__
        invalid = {name: value for name, value in values.items() if value < 0}
        if invalid:
            raise ValueError(f"Reconciliation counts must be nonnegative: {invalid}")
        if self.rows_valid != (
            self.rows_without_keys + self.rows_deduplicated + self.rows_fact_pending
        ):
            raise ValueError(
                "Valid product rows must equal rows without keys, deduplicated rows, "
                "and pending facts"
            )
        if self.is_partial and self.rows_fact_published != 0:
            raise ValueError("Partial ingestions must not publish facts")
        if not self.is_partial and self.rows_fact_published != self.rows_fact_pending:
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
            "SELECT rows_productos_raw FROM ingestion_runs WHERE id = %s",
            (run_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"Ingestion run {run_id} does not exist.")
        rows_raw = row[0]
        if rows_raw is not None and rows_raw != counts.rows_valid + counts.rows_rejected:
            raise ValueError(
                f"Run {run_id} raw product rows ({rows_raw}) do not reconcile with "
                f"valid + rejected ({counts.rows_valid + counts.rows_rejected})."
            )
        _check_rejection_counts(cur, run_id, counts)
        cur.execute(
            """
            UPDATE ingestion_runs
               SET rows_productos_valid = %s,
                   rows_productos_rejected = %s,
                   rows_productos_without_keys = %s,
                   rows_productos_deduplicated = %s,
                   rows_fact_pending = %s,
                   rows_fact_published = %s
             WHERE id = %s
            """,
            (
                counts.rows_valid,
                counts.rows_rejected,
                counts.rows_without_keys,
                counts.rows_deduplicated,
                counts.rows_fact_pending,
                counts.rows_fact_published,
                run_id,
            ),
        )
        if cur.rowcount != 1:
            raise ValueError(f"Ingestion run {run_id} does not exist.")
    log.info("ingestion.reconciliation_recorded", run_id=run_id, **counts.__dict__)


def _check_rejection_counts(cur, run_id: int, counts: ProductReconciliation) -> None:
    """Require the stored rejection counts to match the run's counters.

    ``rejected_rows`` keeps only a sample, so ``rejection_counts`` is the
    persisted evidence behind rows rejected and rows without keys.
    """
    cur.execute(
        """
        SELECT source_file, SUM(row_count)
        FROM rejection_counts
        WHERE run_id = %s AND source_file IN (%s, %s)
        GROUP BY source_file
        """,
        (run_id, CLEANING_SOURCE, KEY_RESOLUTION_SOURCE),
    )
    stored = dict(cur.fetchall())
    expected = {
        CLEANING_SOURCE: counts.rows_rejected,
        KEY_RESOLUTION_SOURCE: counts.rows_without_keys,
    }
    mismatched = {
        source: {"expected": rows, "stored": stored.get(source, 0)}
        for source, rows in expected.items()
        if stored.get(source, 0) != rows
    }
    if mismatched:
        raise ValueError(
            f"Run {run_id} rejection counts do not reconcile: {mismatched}"
        )


def record_product_rejections(
    conn,
    *,
    run_id: int,
    source: str,
    rejected: list[dict],
    sample_rows: int | None = None,
) -> int:
    """Count product-level rejections and keep a bounded sample of them.

    ``source`` identifies the processing step (for example ``clean_productos``
    or ``fact_key_resolution``).  Each item carries a stable ``code``, counted
    in full, and a detailed ``reason`` stored with the sampled row.  Values are
    JSON-encoded with ``str`` fallback because pandas and Decimal values may
    appear in the rejected row payload.
    """
    if not rejected:
        return 0
    samples = (
        (
            item["code"],
            json.dumps(item["row"], default=str, sort_keys=True),
            item["reason"],
        )
        for item in rejected
    )
    return record_rejections(
        conn,
        run_id=run_id,
        source_file=source,
        counts=Counter(item["code"] for item in rejected),
        samples=samples,
        sample_rows=sample_rows,
    )
