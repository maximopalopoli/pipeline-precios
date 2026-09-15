"""Durable mutual exclusion for the publication of one ingestion date."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from uuid import uuid4


DEFAULT_INGESTION_LOCK_LEASE = timedelta(hours=6)


@dataclass(frozen=True)
class IngestionLock:
    """Ownership data returned after reserving an ingestion date."""

    fecha_datos: date
    raw_file_id: int
    owner: str
    token: str
    acquired_at: datetime
    expires_at: datetime


class IngestionLockBusyError(RuntimeError):
    """Raised when another execution already owns an ingestion date."""

    def __init__(self, lock: IngestionLock):
        self.lock = lock
        super().__init__(
            f"Ingestion for {lock.fecha_datos.isoformat()} is already locked "
            f"by {lock.owner} since {lock.acquired_at.isoformat()}."
        )


class IngestionLockOwnershipError(RuntimeError):
    """Raised when an execution tries to release a lock it does not own."""


def acquire_ingestion_lock(
    conn,
    *,
    fecha_datos: date,
    raw_file_id: int,
    owner: str,
    lease: timedelta = DEFAULT_INGESTION_LOCK_LEASE,
) -> IngestionLock:
    """Reserve ``fecha_datos`` for one lifecycle spanning CLI or Airflow.

    The reservation is committed immediately so it remains active across the
    separate database connections used by Airflow tasks. A later execution can
    replace an expired lease, recovering from a process that terminated before
    its failure handler could release the date.
    """
    if lease <= timedelta():
        raise ValueError("Ingestion lock lease must be positive.")

    token = str(uuid4())
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO ingestion_locks (
                fecha_datos, raw_file_id, owner, token, expires_at
            )
            VALUES (%s, %s, %s, %s::uuid, NOW() + %s)
            ON CONFLICT (fecha_datos) DO UPDATE
                SET raw_file_id = EXCLUDED.raw_file_id,
                    owner = EXCLUDED.owner,
                    token = EXCLUDED.token,
                    acquired_at = NOW(),
                    expires_at = EXCLUDED.expires_at
                WHERE ingestion_locks.expires_at <= NOW()
            RETURNING fecha_datos, raw_file_id, owner, token::text, acquired_at, expires_at
            """,
            (fecha_datos, raw_file_id, owner, token, lease),
        )
        row = cur.fetchone()
        if row is None:
            cur.execute(
                """
                SELECT fecha_datos, raw_file_id, owner, token::text, acquired_at, expires_at
                FROM ingestion_locks
                WHERE fecha_datos = %s
                """,
                (fecha_datos,),
            )
            existing = cur.fetchone()
            conn.rollback()
            if existing is None:
                # A concurrent owner released the lock between the INSERT and
                # SELECT.  The caller may retry safely.
                raise IngestionLockBusyError(
                    IngestionLock(
                        fecha_datos,
                        raw_file_id,
                        "unknown",
                        "",
                        datetime.min,
                        datetime.min,
                    )
                )
            raise IngestionLockBusyError(IngestionLock(*existing))
    conn.commit()
    return IngestionLock(*row)


def release_ingestion_lock(conn, lock: IngestionLock) -> None:
    """Release ``lock`` only when its owner token still matches."""
    with conn.cursor() as cur:
        cur.execute(
            """
            DELETE FROM ingestion_locks
            WHERE fecha_datos = %s AND token = %s::uuid
            """,
            (lock.fecha_datos, lock.token),
        )
        released = cur.rowcount == 1
    if not released:
        conn.rollback()
        raise IngestionLockOwnershipError(
            f"Ingestion lock for {lock.fecha_datos.isoformat()} is not owned by this run."
        )
    conn.commit()
