"""Shared price-rule cases checked against Python, SQL and the cleaner.

Each case is ``(id, precio_lista, precio_promo1, precio_promo2, discount,
effective)``.
"""

from decimal import Decimal

D = Decimal

PRICE_CASES = [
    ("no promotion", D("100"), None, None, None, D("100")),
    ("zero promotion", D("100"), D("0"), None, None, D("100")),
    ("negative promotion", D("100"), D("-5"), None, None, D("100")),
    ("promotion above list", D("100"), D("120"), None, None, D("100")),
    ("promotion equal to list", D("100"), D("100"), None, None, D("100")),
    ("one discount", D("100"), D("80"), None, D("80"), D("80")),
    ("lowest of two discounts", D("100"), D("90"), D("80"), D("80"), D("80")),
    ("second promotion only valid", D("100"), D("120"), D("90"), D("90"), D("90")),
    ("zero first promotion", D("100"), D("0"), D("70"), D("70"), D("70")),
    ("missing list price", None, D("80"), None, None, None),
    ("zero list price", D("0"), D("80"), None, None, None),
]
