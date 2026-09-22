from api.routes.health import _reconciliation_response


def test_health_omits_reconciliation_for_legacy_runs():
    row = {
        "rows_productos_raw": None,
        "rows_productos_valid": None,
        "rows_productos_rejected": None,
        "rows_productos_without_keys": None,
        "rows_productos_deduplicated": None,
        "rows_fact_pending": None,
        "rows_fact_published": None,
    }

    assert _reconciliation_response(row) is None


def test_health_exposes_the_persisted_reconciliation_counts():
    row = {
        "rows_productos_raw": 12,
        "rows_productos_valid": 10,
        "rows_productos_rejected": 2,
        "rows_productos_without_keys": 1,
        "rows_productos_deduplicated": 2,
        "rows_fact_pending": 7,
        "rows_fact_published": 7,
    }

    assert _reconciliation_response(row) == row
