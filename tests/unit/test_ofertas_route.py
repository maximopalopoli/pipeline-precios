import asyncio
from datetime import date

from api.routes import ofertas


def test_offers_group_prices_by_their_promotion_conditions(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_fetchrow(sql: str):
        return {"max_fecha": date(2026, 3, 17)}

    async def fake_fetch(sql: str, *params):
        captured["sql"] = sql
        captured["params"] = params
        return []

    monkeypatch.setattr(ofertas, "fetchrow", fake_fetchrow)
    monkeypatch.setattr(ofertas, "fetch", fake_fetch)

    response = asyncio.run(
        ofertas.get_ofertas(provincia=None, localidad=None, cadenas=None)
    )

    assert response == {"fecha": "2026-03-17", "items": []}
    assert captured["params"] == (date(2026, 3, 17),)
    assert "AS leyenda_promo" in captured["sql"]
    assert "GROUP BY p.productos_descripcion, p.productos_marca," in captured["sql"]
    assert "AS condicion" in captured["sql"]
    assert "f.tiene_promo = TRUE AND ps.cualquiera IS NOT NULL" in captured["sql"]
