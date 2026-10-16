"""Migrations 035 and 043 retire the analytics aggregates and benchmark tables."""


def test_analytics_aggregates_are_gone(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT to_regclass('public.agg_precios_diarios'), "
            "to_regclass('public.v_variacion_precios')"
        )
        assert cur.fetchone() == (None, None)


def test_benchmark_metric_tables_are_gone(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT to_regclass('public.ingestion_fact_load_metrics'), "
            "to_regclass('public.ingestion_publication_metrics')"
        )
        assert cur.fetchone() == (None, None)
