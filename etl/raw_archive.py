"""Archive status of completed raw ZIPs.

The weekday SEPA URL is overwritten every week, so a verified archived copy is
the only way to re-ingest a day after its local ZIP is removed.  Archive keys
are derived from the declared date and the content hash, never from the source
file name.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Mapping

import structlog

from etl.extract.downloader import hash_file

log = structlog.get_logger(__name__)

_ARCHIVE_DIR_ENV = "SEPA_ARCHIVE_DIR"


@dataclass(frozen=True)
class UnarchivedRawFile:
    id: int
    filename: str
    sha256: str
    size_bytes: int
    fecha_datos: date


@dataclass(frozen=True)
class RawArchiveResult:
    """Outcome of one pass over the completed raw ZIPs not yet archived."""

    archived_file_ids: tuple[int, ...]
    missing_file_ids: tuple[int, ...]
    failed_file_ids: tuple[int, ...]


def archive_dir_from_environment(
    raw_dir: Path, env: Mapping[str, str] | None = None
) -> Path:
    """Return ``SEPA_ARCHIVE_DIR``, or an ``archive`` folder inside ``raw_dir``."""
    values = os.environ if env is None else env
    configured = values.get(_ARCHIVE_DIR_ENV, "").strip()
    return Path(configured) if configured else raw_dir / "archive"


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


def archive_raw_file(
    raw: UnarchivedRawFile, *, raw_dir: Path, archive_dir: Path
) -> str:
    """Place a verified copy of ``raw`` in ``archive_dir`` and return its key.

    The copy is a hard link when both folders share a file system, so archiving
    local ZIPs costs no extra disk space; otherwise the bytes are copied.  The
    copy is verified against the registered hash and size before it becomes
    visible under its final key, and an existing copy is verified, not replaced.
    """
    key = archive_key_for(raw.fecha_datos, raw.sha256)
    destination = archive_dir / key
    if destination.exists():
        _verify(destination, raw)
        return key

    source = raw_dir / raw.filename
    if source.name != raw.filename:
        raise ValueError(f"Raw filename is not a plain basename: {raw.filename}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_name(f"{destination.name}.part")
    temp.unlink(missing_ok=True)
    try:
        try:
            os.link(source, temp)
        except OSError:
            shutil.copyfile(source, temp)
        _verify(temp, raw)
        temp.replace(destination)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    return key


def archive_completed_raw_files(
    conn, *, raw_dir: Path, archive_dir: Path
) -> RawArchiveResult:
    """Archive every completed raw ZIP still pending, committing each one.

    Pending files include earlier days whose archive failed or that predate the
    archive, so a later successful run retries them.  A ZIP that is no longer
    in ``raw_dir`` cannot be archived and is reported as missing.
    """
    archived: list[int] = []
    missing: list[int] = []
    failed: list[int] = []
    for raw in find_unarchived_raw_files(conn):
        if not (raw_dir / raw.filename).exists():
            missing.append(raw.id)
            continue
        try:
            key = archive_raw_file(raw, raw_dir=raw_dir, archive_dir=archive_dir)
            mark_raw_file_archived(conn, raw_file_id=raw.id, archive_key=key)
            conn.commit()
        except Exception:
            conn.rollback()
            log.exception(
                "raw_archive.failed", raw_file_id=raw.id, filename=raw.filename
            )
            failed.append(raw.id)
            continue
        archived.append(raw.id)
    return RawArchiveResult(tuple(archived), tuple(missing), tuple(failed))


def _verify(path: Path, raw: UnarchivedRawFile) -> None:
    sha256, size_bytes = hash_file(path)
    if (sha256, size_bytes) != (raw.sha256, raw.size_bytes):
        raise ValueError(
            f"Archived copy {path} does not match raw file {raw.id}: "
            f"sha256={sha256} size_bytes={size_bytes}"
        )
