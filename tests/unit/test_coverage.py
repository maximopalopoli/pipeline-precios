"""Coverage codes say why a product's prices compare poorly."""

import pytest

from api.coverage import coverage_notes


@pytest.mark.parametrize(
    "es_ean, id_producto, sucursales, notes",
    [
        (True, "7790000000011", 12, []),
        (True, "7790000000011", None, []),
        # Weighed or cut in the store, whether or not the chain flags it EAN.
        (False, "2507281000005", 40, ["fraccionado_en_local"]),
        (True, "2920723500008", 40, ["fraccionado_en_local"]),
        (False, "0000000732918", 40, ["codigo_interno"]),
        (False, "2045", 40, ["codigo_interno"]),
        (True, "7790000000011", 2, ["pocos_locales"]),
        (False, "2045", 1, ["codigo_interno", "pocos_locales"]),
        # No store is not few stores: the search says no store carries it.
        (True, "7790000000011", 0, []),
    ],
)
def test_coverage_notes(es_ean, id_producto, sucursales, notes):
    assert coverage_notes(es_ean, id_producto, sucursales) == notes
