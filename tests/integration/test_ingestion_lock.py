"""Database-backed behavior of the shared ingestion-date mutex."""

from datetime import date, timedelta

import pytest

from etl.ingestion_lock import (
    IngestionLock,
    IngestionLockBusyError,
    IngestionLockOwnershipError,
    acquire_ingestion_lock,
    release_ingestion_lock,
)


def _create_raw_file(conn, sha256: str, fecha_datos: date) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO raw_files (filename, url, sha256, size_bytes, fecha_datos)
            VALUES (%s, 'http://example.test/data.zip', %s, 1, %s)
            RETURNING id
            """,
            (f"{sha256}.zip", sha256, fecha_datos),
        )
        raw_file_id: int = cur.fetchone()[0]
    conn.commit()
    return raw_file_id


def test_only_one_owner_can_reserve_an_ingestion_date(pg_conn):
    fecha_datos = date(2026, 3, 17)
    first_raw_file_id = _create_raw_file(pg_conn, "lock-first", fecha_datos)
    second_raw_file_id = _create_raw_file(pg_conn, "lock-second", fecha_datos)

    first_lock = acquire_ingestion_lock(
        pg_conn,
        fecha_datos=fecha_datos,
        raw_file_id=first_raw_file_id,
        owner="cli",
    )

    with pytest.raises(IngestionLockBusyError) as exc_info:
        acquire_ingestion_lock(
            pg_conn,
            fecha_datos=fecha_datos,
            raw_file_id=second_raw_file_id,
            owner="airflow",
        )

    assert exc_info.value.lock == first_lock


def test_releasing_a_lock_allows_a_later_retry(pg_conn):
    fecha_datos = date(2026, 3, 18)
    first_raw_file_id = _create_raw_file(pg_conn, "lock-retry-first", fecha_datos)
    second_raw_file_id = _create_raw_file(pg_conn, "lock-retry-second", fecha_datos)
    first_lock = acquire_ingestion_lock(
        pg_conn,
        fecha_datos=fecha_datos,
        raw_file_id=first_raw_file_id,
        owner="airflow",
    )

    release_ingestion_lock(pg_conn, first_lock)

    second_lock = acquire_ingestion_lock(
        pg_conn,
        fecha_datos=fecha_datos,
        raw_file_id=second_raw_file_id,
        owner="cli",
    )
    assert second_lock.raw_file_id == second_raw_file_id


def test_only_the_owner_token_can_release_a_lock(pg_conn):
    fecha_datos = date(2026, 3, 19)
    raw_file_id = _create_raw_file(pg_conn, "lock-owner", fecha_datos)
    lock = acquire_ingestion_lock(
        pg_conn,
        fecha_datos=fecha_datos,
        raw_file_id=raw_file_id,
        owner="cli",
    )
    wrong_lock = IngestionLock(
        fecha_datos=lock.fecha_datos,
        raw_file_id=lock.raw_file_id,
        owner=lock.owner,
        token="00000000-0000-0000-0000-000000000000",
        acquired_at=lock.acquired_at,
        expires_at=lock.expires_at,
    )

    with pytest.raises(IngestionLockOwnershipError):
        release_ingestion_lock(pg_conn, wrong_lock)

    release_ingestion_lock(pg_conn, lock)


def test_expired_lock_can_be_recovered_by_a_later_retry(pg_conn):
    fecha_datos = date(2026, 3, 20)
    first_raw_file_id = _create_raw_file(pg_conn, "lock-expired-first", fecha_datos)
    second_raw_file_id = _create_raw_file(pg_conn, "lock-expired-second", fecha_datos)
    first_lock = acquire_ingestion_lock(
        pg_conn,
        fecha_datos=fecha_datos,
        raw_file_id=first_raw_file_id,
        owner="cli:crashed",
        lease=timedelta(microseconds=1),
    )

    recovered_lock = acquire_ingestion_lock(
        pg_conn,
        fecha_datos=fecha_datos,
        raw_file_id=second_raw_file_id,
        owner="airflow:retry",
    )

    assert recovered_lock.raw_file_id == second_raw_file_id
    assert recovered_lock.token != first_lock.token
    with pytest.raises(IngestionLockOwnershipError):
        release_ingestion_lock(pg_conn, first_lock)
    release_ingestion_lock(pg_conn, recovered_lock)


def test_lock_lease_must_be_positive(pg_conn):
    fecha_datos = date(2026, 3, 21)
    raw_file_id = _create_raw_file(pg_conn, "lock-invalid-lease", fecha_datos)

    with pytest.raises(ValueError, match="must be positive"):
        acquire_ingestion_lock(
            pg_conn,
            fecha_datos=fecha_datos,
            raw_file_id=raw_file_id,
            owner="cli",
            lease=timedelta(),
        )
