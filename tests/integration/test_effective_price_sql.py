from decimal import Decimal

from api.pricing import effective_price_sql


def test_effective_price_sql_selects_the_lowest_positive_fact_price(pg_conn):
    expression = effective_price_sql("price")
    with pg_conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT {expression}
            FROM (
                VALUES
                    (100::numeric, NULL::numeric, NULL::numeric),
                    (100::numeric, 80::numeric, 90::numeric),
                    (100::numeric, 0::numeric, 200::numeric),
                    (NULL::numeric, 0::numeric, NULL::numeric)
            ) AS price(precio_lista, precio_promo1, precio_promo2)
            """
        )
        assert cur.fetchall() == [
            (Decimal("100"),),
            (Decimal("80"),),
            (Decimal("100"),),
            (None,),
        ]
