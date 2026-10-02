"""Location parameters are validated before any query runs."""

import pytest
from fastapi import HTTPException

from api.location import LAT_MAX, MAX_RADIUS_KM, adaptive_radius, optional_location
from api.main import app

LOCATED_ROUTES = [
    "/api/products/search", "/api/buscador", "/api/buscador/generic", "/api/buscador/ean",
    "/api/locales", "/api/location/radius",
]


@pytest.mark.parametrize("lat, lon", [(-34.6, None), (None, -58.4)])
def test_a_coordinate_without_the_other_is_rejected(lat, lon):
    with pytest.raises(HTTPException) as error:
        optional_location(lat, lon, 1)

    assert error.value.status_code == 422


def test_no_coordinates_is_no_location():
    assert optional_location(None, None, 1) is None


@pytest.mark.parametrize("route", LOCATED_ROUTES)
def test_every_located_route_bounds_the_point_to_argentina_and_the_radius(route):
    parameters = {
        p["name"]: p["schema"] for p in app.openapi()["paths"][route]["get"]["parameters"]
    }

    lat = parameters["lat"].get("anyOf", [parameters["lat"]])[0]
    assert lat["maximum"] == LAT_MAX
    assert parameters["radius"]["maximum"] == MAX_RADIUS_KM


@pytest.mark.parametrize(
    "requested, nearest_km, expected",
    [
        (1, [0.2, 0.5, 0.9], (1, 3, False, True)),
        (1, [0.2, 0.5, 1.2], (2, 3, True, True)),
        (0.5, [0.4, 0.6, 0.9, 3], (1, 3, True, True)),
        (3, [0.2, 4, 4.5], (5, 3, True, True)),
        (1, [], (50, 0, True, False)),
    ],
)
def test_the_radius_grows_through_fixed_steps(requested, nearest_km, expected):
    radius = adaptive_radius(requested, nearest_km)

    assert (radius["radio_km"], radius["cadenas"], radius["ampliado"], radius["suficiente"]) == (
        expected
    )
