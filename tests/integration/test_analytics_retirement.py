"""Migration 035 retires the analytics aggregates and their publication timings."""


def test_analytics_aggregates_and_their_timings_are_gone(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT to_regclass('public.agg_precios_diarios'), "
            "to_regclass('public.v_variacion_precios')"
        )
        assert cur.fetchone() == (None, None)
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'ingestion_publication_metrics'
              AND column_name LIKE 'aggregate_%'
            """
        )
        assert cur.fetchall() == []
