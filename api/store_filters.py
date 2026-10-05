"""Store filters of the shopper routes: province, locality, chains, location."""

from __future__ import annotations

from api.location import Location, bind, within_sql


def store_conditions(
    params: list,
    provincia: str | None,
    localidad: str | None,
    cadenas: list[str] | None,
    location: Location | None,
) -> list[str]:
    """Conditions on stores ``s``, chains ``c`` and provinces ``pr``.

    Distance is part of the query, so it applies before any limit.
    """
    conditions = []
    if provincia:
        conditions.append(f"pr.nombre = {bind(params, provincia)}")
    if localidad:
        conditions.append(f"s.sucursales_localidad = {bind(params, localidad)}")
    if cadenas:
        conditions.append(f"c.comercio_bandera_nombre = ANY({bind(params, cadenas)}::text[])")
    if location:
        conditions.append(within_sql(location, params))
    return conditions
