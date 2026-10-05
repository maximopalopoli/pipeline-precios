import asyncio
from datetime import date

from api.routes import ofertas


def test_offers_group_prices_by_product_chain_and_promotion_conditions(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_fetchrow(sql: str):
        return {"fecha_id": 20260317, "fecha": date(2026, 3, 17)}

    async def fake_fetch(sql: str, *params):
        captured["sql"] = sql
        captured["params"] = params
        return [{"total": 0, "descuento_promedio": None, "descuento_maximo": None,
                 "producto_sk": None}]

    monkeypatch.setattr(ofertas, "fetchrow", fake_fetchrow)
    monkeypatch.setattr(ofertas, "fetch", fake_fetch)

    response = asyncio.run(
        ofertas.get_ofertas(
            provincia=None, localidad=None, cadenas=None, location=None, limit=50, offset=0,
        )
    )

    assert response == {
        "fecha": "2026-03-17", "items": [], "total": 0, "hay_mas": False,
        "descuento_promedio": None, "descuento_maximo": None,
    }
    assert captured["params"] == (20260317, 50, 0)
    assert "AS leyenda_promo" in captured["sql"]
    assert "GROUP BY p.producto_sk, p.nombre," in captured["sql"]
    assert "AS condicion" in captured["sql"]
    assert "f.tiene_promo = TRUE AND ps.cualquiera IS NOT NULL" in captured["sql"]
