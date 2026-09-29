"""Configuration and store classification of the regional scope."""

import pytest

from etl.regional_scope import AMBA, StoreScope, scope_from_environment


@pytest.mark.parametrize("configured", [None, "", "  "])
def test_no_scope_keeps_every_store(configured):
    env = {} if configured is None else {"SEPA_REGIONAL_SCOPE": configured}

    assert scope_from_environment(env) is None


@pytest.mark.parametrize("configured", ["amba", " AMBA "])
def test_the_amba_scope_is_selected_by_name(configured):
    assert scope_from_environment({"SEPA_REGIONAL_SCOPE": configured}) is AMBA


def test_an_unknown_scope_fails_fast():
    with pytest.raises(ValueError, match="SEPA_REGIONAL_SCOPE must be one of: amba"):
        scope_from_environment({"SEPA_REGIONAL_SCOPE": "patagonia"})


@pytest.mark.parametrize(
    ("latitude", "longitude", "provincia", "expected"),
    [
        # Palermo, CABA
        ("-34.5875", "-58.4250", "AR-C", StoreScope.IN_SCOPE),
        # La Plata, about 53 km from the Obelisco
        ("-34.9214", "-57.9545", "AR-B", StoreScope.IN_SCOPE),
        # Luján, about 66 km from the Obelisco
        ("-34.5703", "-59.1050", "AR-B", StoreScope.OUT_OF_SCOPE),
        # Mar del Plata
        ("-38.0055", "-57.5426", "AR-B", StoreScope.OUT_OF_SCOPE),
        # Córdoba: the province alone decides
        ("-31.4201", "-64.1888", "AR-X", StoreScope.OUT_OF_SCOPE),
        ("", "", "AR-X", StoreScope.OUT_OF_SCOPE),
        # A store near the center with an invalid province is not guessed in
        ("-34.6037", "-58.3816", "", StoreScope.OUT_OF_SCOPE),
        # All of CABA is inside the radius, with or without coordinates
        ("", "", "AR-C", StoreScope.IN_SCOPE),
        ("0", "0", "AR-C", StoreScope.IN_SCOPE),
        # Buenos Aires province without plausible coordinates is unlocated
        ("", "", "AR-B", StoreScope.UNLOCATED),
        ("0", "0", "AR-B", StoreScope.UNLOCATED),
        ("-34,6", "-58,4", "AR-B", StoreScope.UNLOCATED),
        ("nan", "nan", "AR-B", StoreScope.UNLOCATED),
        # Values are trimmed like the other staging text
        (" -34.5875 ", " -58.4250 ", " AR-B ", StoreScope.IN_SCOPE),
    ],
)
def test_amba_classifies_stores(latitude, longitude, provincia, expected):
    assert AMBA.classify(latitude, longitude, provincia) is expected
