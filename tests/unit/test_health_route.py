import asyncio
from datetime import date, datetime

from api.routes import health


def test_health_omits_reconciliation_for_legacy_runs():
    row = {
        "rows_productos_raw": None,
        "rows_productos_selected": None,
        "rows_productos_valid": None,
        "rows_productos_rejected": None,
        "rows_productos_without_keys": None,
        "rows_productos_deduplicated": None,
        "rows_fact_pending": None,
        "rows_fact_published": None,
    }

    assert health._reconciliation_response(row) is None


def test_health_exposes_the_persisted_reconciliation_counts():
    row = {
        "rows_productos_raw": 12,
        "rows_productos_selected": 12,
        "rows_productos_valid": 10,
        "rows_productos_rejected": 2,
        "rows_productos_without_keys": 1,
        "rows_productos_deduplicated": 2,
        "rows_fact_pending": 7,
        "rows_fact_published": 7,
    }

    assert health._reconciliation_response(row) == row


def test_health_returns_no_reconciliation_when_no_ingestion_exists(monkeypatch):
    async def fake_fetchrow(sql: str):
        return None

    monkeypatch.setattr(health, "fetchrow", fake_fetchrow)

    assert asyncio.run(health.get_health()) == {
        "status": "no_data",
        "started_at": None,
        "fecha_datos": None,
        "is_partial": None,
        "reconciliacion": None,
    }


def test_health_returns_reconciliation_for_the_latest_ingestion(monkeypatch):
    captured: dict[str, str] = {}
    row = {
        "status": "completed",
        "started_at": datetime(2026, 3, 18, 12, 30),
        "fecha_datos": date(2026, 3, 17),
        "is_partial": True,
        "rows_productos_raw": 12,
        "rows_productos_selected": 2,
        "rows_productos_valid": 10,
        "rows_productos_rejected": 2,
        "rows_productos_without_keys": 1,
        "rows_productos_deduplicated": 2,
        "rows_fact_pending": 7,
        "rows_fact_published": 7,
    }

    async def fake_fetchrow(sql: str):
        captured["sql"] = sql
        return row

    monkeypatch.setattr(health, "fetchrow", fake_fetchrow)

    assert asyncio.run(health.get_health()) == {
        "status": "completed",
        "started_at": "2026-03-18T12:30:00",
        "fecha_datos": "2026-03-17",
        "is_partial": True,
        "reconciliacion": {
            key: value
            for key, value in row.items()
            if key.startswith("rows_")
        },
    }
    assert "ir.rows_productos_rejected" in captured["sql"]
    assert "ir.rows_fact_published" in captured["sql"]
