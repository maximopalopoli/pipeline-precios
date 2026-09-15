import asyncio

from api.routes import locales


def test_store_promos_only_requests_effective_discounts(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_fetchrow(sql: str):
        return {"fecha_id": 20260317}

    async def fake_fetch(sql: str, *params):
        captured["sql"] = sql
        captured["params"] = params
        return []

    monkeypatch.setattr(locales, "fetchrow", fake_fetchrow)
    monkeypatch.setattr(locales, "fetch", fake_fetch)

    assert asyncio.run(locales.get_store_promos(42)) == []
    assert captured["params"] == (42, 20260317)
    assert (
        "AND pipeline_effective_price(f.precio_lista, f.precio_promo1, "
        "f.precio_promo2) < f.precio_lista" in captured["sql"]
    )
