from decimal import Decimal

from tests.price_cases import PRICE_CASES


def test_sql_price_functions_follow_the_shared_rule(pg_conn):
    rows = [(lista, p1, p2) for _, lista, p1, p2, _, _ in PRICE_CASES]
    values = ", ".join(["(%s::numeric, %s::numeric, %s::numeric)"] * len(rows))
    with pg_conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT
                pipeline_discount_promo_price(lista, promo1, promo2),
                pipeline_effective_price(lista, promo1, promo2)
            FROM (VALUES {values}) AS price(lista, promo1, promo2)
            """,
            [value for row in rows for value in row],
        )
        result = cur.fetchall()
    assert result == [
        (discount, effective) for _, _, _, _, discount, effective in PRICE_CASES
    ]


def test_store_price_without_promotion_terms_matches_the_effective_price(pg_conn):
    """Legends without requirements or dates leave migration 036's rule intact."""
    rows = [(lista, p1, p2) for _, lista, p1, p2, _, _ in PRICE_CASES]
    values = ", ".join(["(%s::numeric, %s::numeric, %s::numeric)"] * len(rows))
    with pg_conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT pipeline_store_price(
                lista, promo1, NULL::promo_leyendas, promo2, NULL::promo_leyendas,
                DATE '2026-03-24'
            )
            FROM (VALUES {values}) AS price(lista, promo1, promo2)
            """,
            [value for row in rows for value in row],
        )
        result = [row[0] for row in cur.fetchall()]
    assert result == [effective for *_, effective in PRICE_CASES]
    assert Decimal("80") in result
