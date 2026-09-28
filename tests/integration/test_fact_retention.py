"""Partition-safe retention of detailed facts."""

from datetime import date

from etl.fact_retention import find_expired_fact_partitions, purge_expired_fact_partitions
from etl.load.warehouse_loader import _ensure_partition
from etl.retention_policy import RetentionPolicy
from etl.fact_retention import apply_fact_retention


def _create_partitions(conn, *fecha_ids: int) -> None:
    for fecha_id in fecha_ids:
        _ensure_partition(conn, fecha_id)
    conn.commit()


def _exists(conn, table: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s)", (f"public.{table}",))
        return cur.fetchone()[0] is not None


def test_retention_selects_days_then_complete_months_before_the_cutoff(pg_conn):
    _create_partitions(pg_conn, 20250101, 20250201, 20250301, 20250401)

    partitions = find_expired_fact_partitions(pg_conn, cutoff=date(2025, 4, 1))

    assert partitions == (
        "fact_precios_20250101",
        "fact_precios_20250201",
        "fact_precios_20250301",
        "fact_precios_202501",
        "fact_precios_202502",
        "fact_precios_202503",
    )


def test_retention_selects_expired_days_inside_the_current_month(pg_conn):
    _create_partitions(pg_conn, 20250321, 20250322, 20250323, 20250324)

    partitions = find_expired_fact_partitions(pg_conn, cutoff=date(2025, 3, 23))

    assert partitions == ("fact_precios_20250321", "fact_precios_20250322")


def test_dry_run_never_drops_the_selected_partitions(pg_conn):
    _create_partitions(pg_conn, 20250101)

    result = purge_expired_fact_partitions(
        pg_conn, cutoff=date(2025, 2, 1), dry_run=True
    )

    assert result.partitions == ("fact_precios_20250101", "fact_precios_202501")
    assert result.dropped is False
    assert _exists(pg_conn, "fact_precios_202501")
    assert _exists(pg_conn, "fact_precios_20250101")


def test_explicit_non_dry_run_drops_only_expired_whole_partitions(pg_conn):
    _create_partitions(pg_conn, 20250101, 20250201)

    result = purge_expired_fact_partitions(
        pg_conn, cutoff=date(2025, 2, 1), dry_run=False
    )
    pg_conn.commit()

    assert result.partitions == ("fact_precios_20250101", "fact_precios_202501")
    assert result.dropped is True
    assert not _exists(pg_conn, "fact_precios_202501")
    assert not _exists(pg_conn, "fact_precios_20250101")
    assert _exists(pg_conn, "fact_precios_202502")
    assert _exists(pg_conn, "fact_precios_20250201")
    with pg_conn.cursor() as cur:
        cur.execute("SELECT kind, cutoff, targets FROM retention_events")
        assert cur.fetchone() == (
            "fact_partitions",
            date(2025, 2, 1),
            ["fact_precios_20250101", "fact_precios_202501"],
        )


def test_day_level_retention_keeps_the_month_with_retained_days(pg_conn):
    _create_partitions(pg_conn, 20250320, 20250321, 20250322, 20250323, 20250324)

    result = apply_fact_retention(
        pg_conn,
        policy=RetentionPolicy(fact_retention_days=3),
        fecha_datos=date(2025, 3, 24),
    )

    assert result is not None
    assert result.partitions == ("fact_precios_20250320",)
    assert not _exists(pg_conn, "fact_precios_20250320")
    for fecha_id in (20250321, 20250322, 20250323, 20250324):
        assert _exists(pg_conn, f"fact_precios_{fecha_id}")
    assert _exists(pg_conn, "fact_precios_202503")
    # The retained leaves remain attached, so the parent still routes reads.
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT count(*)
            FROM pg_inherits inheritance
            JOIN pg_class parent ON parent.oid = inheritance.inhparent
            WHERE parent.relname = 'fact_precios_202503'
            """
        )
        assert cur.fetchone()[0] == 4


def test_day_level_retention_across_a_month_boundary(pg_conn):
    _create_partitions(pg_conn, 20250330, 20250331, 20250401, 20250402)

    result = apply_fact_retention(
        pg_conn,
        policy=RetentionPolicy(fact_retention_days=1),
        fecha_datos=date(2025, 4, 2),
    )

    assert result is not None
    assert result.partitions == (
        "fact_precios_20250330",
        "fact_precios_20250331",
        "fact_precios_202503",
    )
    assert not _exists(pg_conn, "fact_precios_202503")
    assert _exists(pg_conn, "fact_precios_20250401")
    assert _exists(pg_conn, "fact_precios_20250402")


def test_cli_retention_hook_applies_only_an_enabled_policy(pg_conn):
    _create_partitions(pg_conn, 20250101, 20250201)

    disabled = apply_fact_retention(
        pg_conn,
        policy=RetentionPolicy(),
        fecha_datos=date(2025, 2, 15),
    )
    enabled = apply_fact_retention(
        pg_conn,
        policy=RetentionPolicy(fact_retention_days=14),
        fecha_datos=date(2025, 2, 15),
    )

    assert disabled is None
    assert enabled is not None
    assert enabled.partitions == ("fact_precios_20250101", "fact_precios_202501")
    assert not _exists(pg_conn, "fact_precios_202501")
    assert _exists(pg_conn, "fact_precios_202502")
    assert _exists(pg_conn, "fact_precios_20250201")
