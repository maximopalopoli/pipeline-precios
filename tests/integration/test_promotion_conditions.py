"""Promotion legends decide whether a promotion sets the store price."""

from datetime import date
from decimal import Decimal

import pytest

D = Decimal
ON_DATE = date(2026, 3, 24)


def _scalar(pg_conn, sql: str, params: tuple):
    with pg_conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()[0]


@pytest.mark.parametrize(
    "leyenda, condicion",
    [
        ("Promo A valida desde el 17/03/2026 hasta 24/03/2026", None),
        ("DEL 01/03/2026 AL 05/04/2026", None),
        ("10% de descuento con cualquier medio de pago - Vigencia: Desde el "
         "16/03/2026 Hasta el 29/03/2026", None),
        ("40% de descuento. HUEVO C.GDE. X 6 UD - Vigencia: Desde el 21/03/2026", None),
        ("35% de descuento. 35% GLACIAR 2LT", None),
        ("25% de descuento. 2DO AL 70% HUEVOS FI", "cantidad"),
        ("33% de descuento. 3X2 JUGOS TUTTI", "cantidad"),
        ("40% de descuento. 2X$2500 ALFJ ARROZ G", "cantidad"),
        ("Llevando 2 paga $1390.00 c/u - Vigencia desde el 19/03/2026", "cantidad"),
        ("25% de descuento. 2DA UNIDAD", "cantidad"),
        ("30% con tarjeta Visa", "medio_pago"),
        ("20% pagando con Mercado Pago", "medio_pago"),
        ("15% para socios del club", "programa"),
        ("25% de descuento. 25% PANIF X TARDE", "horario"),
        (None, None),
    ],
)
def test_promotion_condition_is_read_from_the_legend(pg_conn, leyenda, condicion):
    assert _scalar(pg_conn, "SELECT pipeline_promo_condition(%s)", (leyenda,)) == condicion


@pytest.mark.parametrize(
    "leyenda, desde, hasta",
    [
        ("Promo A valida desde el 17/03/2026 hasta 24/03/2026",
         date(2026, 3, 17), date(2026, 3, 24)),
        ("DEL 01/03/2026 AL 05/04/2026", date(2026, 3, 1), date(2026, 4, 5)),
        ("Vigencia desde el 02/03/2026 al 31/03/2026 hasta agotar Stock de 1 unidades.",
         date(2026, 3, 2), date(2026, 3, 31)),
        ("Vigente hasta el 2026-03-25  hasta agotar Stock de 6 unidades.",
         None, date(2026, 3, 25)),
        ("2DO AL 70% - Vigencia: Desde el 18/03/2026 hasta el 24/03/2026",
         date(2026, 3, 18), date(2026, 3, 24)),
        ("Precio Promocional Exclusivo DIA Hasta Agotar Stock o Finalice Vigencia.",
         None, None),
    ],
)
def test_promotion_validity_is_read_from_the_legend(pg_conn, leyenda, desde, hasta):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT pipeline_promo_valid_from(%s), pipeline_promo_valid_until(%s)",
            (leyenda, leyenda),
        )
        assert cur.fetchone() == (desde, hasta)


@pytest.mark.parametrize(
    "precio_promo1, leyenda1, precio_promo2, leyenda2, store_price",
    [
        pytest.param(D("80"), "20% de descuento", None, None, D("80"),
                     id="unconditional promotion"),
        pytest.param(D("60"), "3X2 JUGOS", None, None, D("100"),
                     id="conditional promotion only"),
        pytest.param(D("80"), "20% de descuento", D("60"), "2X1 OBLEAS", D("80"),
                     id="unconditional beside a lower conditional"),
        pytest.param(D("70"), "DEL 01/03/2026 AL 23/03/2026", None, None, D("100"),
                     id="expired"),
        pytest.param(D("70"), "DEL 25/03/2026 AL 31/03/2026", None, None, D("100"),
                     id="not started"),
        pytest.param(D("70"), "DEL 01/03/2026 AL 24/03/2026", None, None, D("70"),
                     id="last valid day"),
        pytest.param(D("120"), "20% de descuento", None, None, D("100"),
                     id="above the list price"),
    ],
)
def test_store_price_uses_only_unconditional_promotions_in_force(
    pg_conn, precio_promo1, leyenda1, precio_promo2, leyenda2, store_price
):
    assert _scalar(
        pg_conn,
        """
        SELECT pipeline_store_price(
            100, %s,
            pipeline_promo_terms(%s, NULL::promo_leyendas),
            %s,
            pipeline_promo_terms(%s, NULL::promo_leyendas),
            %s
        )
        """,
        (precio_promo1, leyenda1, precio_promo2, leyenda2, ON_DATE),
    ) == store_price


def test_store_price_is_null_without_a_valid_list_price(pg_conn):
    assert _scalar(
        pg_conn,
        """
        SELECT pipeline_store_price(
            0, 80, pipeline_promo_terms('20%%', NULL::promo_leyendas),
            NULL, NULL::promo_leyendas, %s
        )
        """,
        (ON_DATE,),
    ) is None


def test_slots_choose_the_lowest_promotion_of_each_kind(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            WITH terms AS (
                SELECT
                    pipeline_promo_terms('2X1 OBLEAS', NULL::promo_leyendas) AS t1,
                    pipeline_promo_terms('2DO AL 70%%', NULL::promo_leyendas) AS t2
            )
            SELECT
                pipeline_promo_slot(100, 60, t1, 65, t2, %s, TRUE),
                pipeline_promo_slot(100, 60, t1, 65, t2, %s, FALSE),
                pipeline_promo_slot(100, 60, t1, 60, t2, %s, NULL)
            FROM terms
            """,
            (ON_DATE, ON_DATE, ON_DATE),
        )
        assert cur.fetchone() == (1, None, 1)


def test_a_stored_legend_reading_takes_precedence(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO promo_leyendas (leyenda, condicion)
            VALUES ('Promo A', 'programa')
            """
        )
    assert _scalar(
        pg_conn,
        """
        SELECT pipeline_store_price(100, 80, pipeline_promo_terms('Promo A', l), NULL,
                                    NULL::promo_leyendas, %s)
        FROM promo_leyendas l WHERE l.leyenda = 'Promo A'
        """,
        (ON_DATE,),
    ) == D("100")
