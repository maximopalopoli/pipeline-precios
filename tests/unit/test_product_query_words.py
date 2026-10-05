"""Search queries are split into words as the database normalizes text."""

import pytest

from api.routes.products import MAX_WORDS, barcode, query_words


@pytest.mark.parametrize(
    "q, words",
    [
        ("leche", ["LECHE"]),
        ("Té  Ñandú", ["TE", "NANDU"]),
        ("aceite girasol 1.5 l", ["ACEITE", "GIRASOL"]),
        ("S/GAS coca-cola", ["GAS", "COCA", "COLA"]),
        ("leche LECHE", ["LECHE"]),
        ("x", []),
        ("7790742348005", ["7790742348005"]),
    ],
)
def test_query_words(q, words):
    assert query_words(q) == words


def test_a_long_query_keeps_its_first_words():
    assert len(query_words(" ".join(f"palabra{i}" for i in range(20)))) == MAX_WORDS


@pytest.mark.parametrize(
    "q, code",
    [
        ("7790742348005", "7790742348005"),
        (" 779 0742-348005 ", "7790742348005"),
        ("77900124", "0000077900124"),
        ("012000001291", "0012000001291"),
        ("1234567", None),
        ("77907423480051", None),
        ("leche 1000", None),
        ("", None),
    ],
)
def test_barcode(q, code):
    assert barcode(q) == code
