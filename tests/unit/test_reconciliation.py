import pytest

from etl.reconciliation import ProductReconciliation


def test_product_reconciliation_accepts_explained_rows():
    counts = ProductReconciliation(
        rows_valid=10,
        rows_rejected=2,
        rows_without_keys=1,
        rows_deduplicated=2,
        rows_fact_pending=7,
        rows_fact_published=7,
    )

    assert counts.rows_valid == 10


@pytest.mark.parametrize(
    "kwargs",
    [
        {"rows_valid": -1},
        {"rows_without_keys": 2, "rows_fact_pending": 7},
        {"rows_fact_published": 6},
    ],
)
def test_product_reconciliation_rejects_unexplained_or_negative_counts(kwargs):
    values = {
        "rows_valid": 10,
        "rows_rejected": 2,
        "rows_without_keys": 1,
        "rows_deduplicated": 2,
        "rows_fact_pending": 7,
        "rows_fact_published": 7,
    }
    values.update(kwargs)

    with pytest.raises(ValueError):
        ProductReconciliation(**values)
