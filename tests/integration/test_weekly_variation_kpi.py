"""Integration coverage for the weekly KPI methodology."""

from datetime import date
from decimal import Decimal

from api.kpi_metrics import WEEKLY_VARIATION_SQL


def _seed_calendar(conn, *dates: date) -> None:
    with conn.cursor() as cur:
        for value in dates:
            cur.execute(
                """
                INSERT INTO dim_fecha (
                    fecha_id, fecha, anio, mes, trimestre, semana_iso,
                    dia_semana, es_fin_semana
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    int(value.strftime("%Y%m%d")),
                    value,
                    value.year,
                    value.month,
                    (value.month - 1) // 3 + 1,
                    value.isocalendar().week,
                    value.isoweekday(),
                    value.isoweekday() >= 6,
                ),
            )


def _seed_product(conn, number: int) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO dim_producto (producto_identity, id_producto)
            VALUES (%s, %s)
            RETURNING producto_sk
            """,
            (f"test:{number}", f"test-{number}"),
        )
        return cur.fetchone()[0]


def _insert_average(conn, product_sk: int, value_date: date, price: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO agg_precios_diarios (
                producto_sk, fecha_id, provincia_sk, precio_lista_avg
            )
            VALUES (%s, %s, 1, %s)
            """,
            (product_sk, int(value_date.strftime("%Y%m%d")), Decimal(price)),
        )


def test_weekly_variation_compares_exact_endpoints_and_excludes_incomplete_series(pg_conn):
    initial = date(2026, 3, 10)
    middle = date(2026, 3, 14)
    latest = date(2026, 3, 17)
    _seed_calendar(pg_conn, initial, middle, latest)
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO dim_provincia (provincia_sk, codigo_iso, nombre, region)
            VALUES (1, 'AR-TST', 'Test', 'AMBA')
            """
        )

    increase = _seed_product(pg_conn, 1)
    decrease = _seed_product(pg_conn, 2)
    volatile = _seed_product(pg_conn, 3)
    incomplete = _seed_product(pg_conn, 4)
    _insert_average(pg_conn, increase, initial, "100")
    _insert_average(pg_conn, increase, latest, "110")
    _insert_average(pg_conn, decrease, initial, "100")
    _insert_average(pg_conn, decrease, latest, "90")
    _insert_average(pg_conn, volatile, initial, "100")
    _insert_average(pg_conn, volatile, middle, "200")
    _insert_average(pg_conn, volatile, latest, "110")
    _insert_average(pg_conn, incomplete, latest, "100")
    pg_conn.commit()

    with pg_conn.cursor() as cur:
        cur.execute(WEEKLY_VARIATION_SQL)
        variation, series, start, end = cur.fetchone()

    # +10%, -10% and +10%: the middle observation and incomplete series do
    # not alter the endpoint comparison.
    assert variation == Decimal("3.33")
    assert series == 3
    assert (start, end) == (initial, latest)


def test_weekly_variation_is_null_without_both_window_endpoints(pg_conn):
    latest = date(2026, 3, 17)
    _seed_calendar(pg_conn, latest)
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO dim_provincia (provincia_sk, codigo_iso, nombre, region)
            VALUES (1, 'AR-TST', 'Test', 'AMBA')
            """
        )
    product = _seed_product(pg_conn, 1)
    _insert_average(pg_conn, product, latest, "100")
    pg_conn.commit()

    with pg_conn.cursor() as cur:
        cur.execute(WEEKLY_VARIATION_SQL)
        variation, series, start, end = cur.fetchone()

    assert variation is None
    assert series == 0
    assert (start, end) == (date(2026, 3, 10), latest)
