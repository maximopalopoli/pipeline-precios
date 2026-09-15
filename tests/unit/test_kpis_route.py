import asyncio
from datetime import date

from api.routes import kpis


def test_weekly_indicator_contract_exposes_methodology_and_coverage():
    response = kpis._weekly_variation_response(
        {
            "variacion_semanal": 1.25,
            "fecha_inicio": date(2026, 3, 10),
            "fecha_fin": date(2026, 3, 17),
            "series_comparadas": 42,
        }
    )

    assert response == {
        "valor": 1.25,
        "fecha_inicio": "2026-03-10",
        "fecha_fin": "2026-03-17",
        "series_comparadas": 42,
        "metodologia": kpis.WEEKLY_VARIATION_METHODOLOGY,
    }


def test_kpis_keeps_legacy_value_while_exposing_the_weekly_indicator(monkeypatch):
    weekly_row = {
        "variacion_semanal": 0,
        "fecha_inicio": date(2026, 3, 10),
        "fecha_fin": date(2026, 3, 17),
        "series_comparadas": 3,
    }
    rows = iter(
        [
            weekly_row,
            {"n": 4},
            {"n": 5},
            {"pct_promo": 10},
            {"cadena": "Cadena", "pct_promo": 20},
        ]
    )

    async def fake_fetchrow(sql: str):
        return next(rows)

    async def fake_fetch(sql: str):
        return []

    monkeypatch.setattr(kpis, "fetchrow", fake_fetchrow)
    monkeypatch.setattr(kpis, "fetch", fake_fetch)

    response = asyncio.run(kpis.get_kpis())

    assert response["inflacion_semanal"] == 0.0
    assert response["variacion_semanal"] == {
        "valor": 0.0,
        "fecha_inicio": "2026-03-10",
        "fecha_fin": "2026-03-17",
        "series_comparadas": 3,
        "metodologia": kpis.WEEKLY_VARIATION_METHODOLOGY,
    }
