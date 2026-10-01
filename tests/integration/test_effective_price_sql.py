from decimal import Decimal

from api.pricing import (
    discount_promo_label_sql,
    discount_promo_price_sql,
    effective_price_sql,
)
from tests.price_cases import PRICE_CASES


def _select(pg_conn, expressions, rows):
    values = ", ".join(["(%s::numeric, %s::numeric, %s::numeric, %s, %s)"] * len(rows))
    params = [value for row in rows for value in row]
    with pg_conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT {", ".join(expressions)}
            FROM (VALUES {values}) AS price(
                precio_lista, precio_promo1, precio_promo2,
                leyenda_promo1, leyenda_promo2
            )
            """,
            params,
        )
        return cur.fetchall()


def test_sql_price_functions_follow_the_shared_rule(pg_conn):
    rows = [(lista, p1, p2, None, None) for _, lista, p1, p2, _, _ in PRICE_CASES]
    result = _select(
        pg_conn, [discount_promo_price_sql("price"), effective_price_sql("price")], rows
    )
    assert result == [
        (discount, effective) for _, _, _, _, discount, effective in PRICE_CASES
    ]


def test_discount_label_belongs_to_the_selected_promotion(pg_conn):
    D = Decimal
    rows = [
        (D("100"), D("80"), D("80"), "promo 1", "promo 2"),
        (D("100"), D("120"), D("90"), "promo 1", "promo 2"),
        (D("100"), D("90"), D("80"), "promo 1", "promo 2"),
        (D("100"), None, None, "promo 1", "promo 2"),
        (D("100"), D("0"), None, "promo 1", "promo 2"),
    ]
    result = _select(pg_conn, [discount_promo_label_sql("price")], rows)
    assert result == [("promo 1",), ("promo 2",), ("promo 2",), (None,), (None,)]
