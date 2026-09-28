"""Archive status of completed raw ZIPs.

The weekday SEPA URL is overwritten every week, so a verified archived copy is
the only way to re-ingest a day after its local ZIP is removed.  Archive keys
are derived from the declared date and the content hash, never from the source
file name.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import structlog

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class UnarchivedRawFile:
    id: int
    filename: str
    sha256: str
    size_bytes: int
    fecha_datos: date


def archive_key_for(fecha_datos: date, sha256: str) -> str:
    """Return the immutable archive location of one source ZIP version."""
    return f"{fecha_datos:%Y/%m}/{fecha_datos.isoformat()}_{sha256}.zip"


def find_unarchived_raw_files(conn) -> tuple[UnarchivedRawFile, ...]:
    """Return raw ZIPs whose latest ingestion completed and are not archived."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT raw.id, raw.filename, raw.sha256, raw.size_bytes, raw.fecha_datos
            FROM raw_files raw
            WHERE raw.archived_at IS NULL
              AND (
                  SELECT latest.status
                  FROM ingestion_runs latest
                  WHERE latest.raw_file_id = raw.id
                  ORDER BY latest.started_at DESC, latest.id DESC
                  LIMIT 1
              ) = 'completed'
            ORDER BY raw.fecha_datos, raw.id
            """
        )
        return tuple(UnarchivedRawFile(*row) for row in cur.fetchall())


def mark_raw_file_archived(conn, *, raw_file_id: int, archive_key: str) -> None:
    """Record a verified archived copy; the caller owns the transaction."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE raw_files
            SET archive_key = %s, archived_at = NOW()
            WHERE id = %s AND archived_at IS NULL
            """,
            (archive_key, raw_file_id),
        )
        if cur.rowcount != 1:
            raise ValueError(f"Raw file {raw_file_id} is missing or already archived")
    log.info("raw_archive.recorded", raw_file_id=raw_file_id, archive_key=archive_key)
