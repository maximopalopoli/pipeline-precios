"""Partition-safe retention of detailed facts."""

from datetime import date

from etl.fact_retention import find_expired_fact_partitions, purge_expired_fact_partitions
from etl.load.warehouse_loader import _ensure_partition


def _create_monthly_partitions(conn, *fecha_ids: int) -> None:
    for fecha_id in fecha_ids:
        _ensure_partition(conn, fecha_id)
    conn.commit()


def test_retention_selects_only_complete_months_before_the_cutoff(pg_conn):
    _create_monthly_partitions(pg_conn, 20250101, 20250201, 20250301, 20250401)

    partitions = find_expired_fact_partitions(pg_conn, cutoff=date(2025, 4, 1))

    assert partitions == (
        "fact_precios_202501",
        "fact_precios_202502",
        "fact_precios_202503",
    )


def test_dry_run_never_drops_the_selected_partitions(pg_conn):
    _create_monthly_partitions(pg_conn, 20250101)

    result = purge_expired_fact_partitions(
        pg_conn, cutoff=date(2025, 2, 1), dry_run=True
    )

    assert result.partitions == ("fact_precios_202501",)
    assert result.dropped is False
    with pg_conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.fact_precios_202501')")
        assert cur.fetchone()[0] == "fact_precios_202501"


def test_explicit_non_dry_run_drops_only_expired_whole_partitions(pg_conn):
    _create_monthly_partitions(pg_conn, 20250101, 20250201)

    result = purge_expired_fact_partitions(
        pg_conn, cutoff=date(2025, 2, 1), dry_run=False
    )
    pg_conn.commit()

    assert result.partitions == ("fact_precios_202501",)
    assert result.dropped is True
    with pg_conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.fact_precios_202501')")
        assert cur.fetchone()[0] is None
        cur.execute("SELECT to_regclass('public.fact_precios_202502')")
        assert cur.fetchone()[0] == "fact_precios_202502"
