"""Durable mutual exclusion for the publication of one ingestion date."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from uuid import uuid4


@dataclass(frozen=True)
class IngestionLock:
    """Ownership data returned after reserving an ingestion date."""

    fecha_datos: date
    raw_file_id: int
    owner: str
    token: str
    acquired_at: datetime


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
) -> IngestionLock:
    """Reserve ``fecha_datos`` for one lifecycle spanning CLI or Airflow.

    The reservation is committed immediately so it remains active across the
    separate database connections used by Airflow tasks.  Callers must release
    the returned token after a completed or failed lifecycle.
    """
    token = str(uuid4())
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO ingestion_locks (fecha_datos, raw_file_id, owner, token)
            VALUES (%s, %s, %s, %s::uuid)
            ON CONFLICT (fecha_datos) DO NOTHING
            RETURNING fecha_datos, raw_file_id, owner, token::text, acquired_at
            """,
            (fecha_datos, raw_file_id, owner, token),
        )
        row = cur.fetchone()
        if row is None:
            cur.execute(
                """
                SELECT fecha_datos, raw_file_id, owner, token::text, acquired_at
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
                    IngestionLock(fecha_datos, raw_file_id, "unknown", "", datetime.min)
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
