"""Safe, opt-in retention of local raw ZIP files."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import structlog

from etl.retention_audit import record_retention_event
from etl.retention_policy import RetentionPolicy

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class RawFile:
    id: int
    filename: str


@dataclass(frozen=True)
class RawRetentionResult:
    """Raw ZIP registry entries considered and local files removed."""

    raw_files: tuple[RawFile, ...]
    deleted_file_ids: tuple[int, ...]
    missing_file_ids: tuple[int, ...]
    deleted: bool


def find_expired_raw_files(conn, *, cutoff: date) -> tuple[RawFile, ...]:
    """Return old raw ZIPs that have completed and no unresolved executions."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT raw.id, raw.filename
            FROM raw_files raw
            WHERE raw.fecha_datos < %s
              AND EXISTS (
                  SELECT 1 FROM ingestion_runs completed
                  WHERE completed.raw_file_id = raw.id
                    AND completed.status = 'completed'
              )
              AND NOT EXISTS (
                  SELECT 1 FROM ingestion_runs unresolved
                  WHERE unresolved.raw_file_id = raw.id
                    AND unresolved.status IN ('running', 'failed')
              )
            ORDER BY raw.id
            """,
            (cutoff,),
        )
        return tuple(RawFile(*row) for row in cur.fetchall())


def purge_expired_raw_files(
    conn,
    *,
    raw_dir: Path,
    cutoff: date,
    dry_run: bool = True,
) -> RawRetentionResult:
    """Optionally remove eligible ZIPs from ``raw_dir`` without DB deletion.

    The registry rows remain so the source version and its completed ingestion
    stay traceable. A later duplicate download restores a missing local ZIP
    under its immutable hash filename.
    """
    raw_files = find_expired_raw_files(conn, cutoff=cutoff)
    if dry_run or not raw_files:
        return RawRetentionResult(raw_files, (), (), False)

    root = raw_dir.resolve()
    deleted_file_ids: list[int] = []
    missing_file_ids: list[int] = []
    for raw_file in raw_files:
        path = _path_in_raw_dir(root, raw_file.filename)
        if not path.exists():
            missing_file_ids.append(raw_file.id)
            continue
        path.unlink()
        deleted_file_ids.append(raw_file.id)

    if deleted_file_ids:
        record_retention_event(
            conn,
            kind="raw_files",
            cutoff=cutoff,
            targets=deleted_file_ids,
        )
    result = RawRetentionResult(
        raw_files,
        tuple(deleted_file_ids),
        tuple(missing_file_ids),
        True,
    )
    log.info("retention.raw_files_deleted", cutoff=str(cutoff), **result.__dict__)
    return result


def apply_raw_retention(
    conn,
    *,
    raw_dir: Path,
    policy: RetentionPolicy,
    fecha_datos: date,
) -> RawRetentionResult | None:
    """Apply enabled raw ZIP retention after a successful ingestion."""
    cutoff = policy.raw_cutoff(fecha_datos)
    if cutoff is None:
        return None
    result = purge_expired_raw_files(conn, raw_dir=raw_dir, cutoff=cutoff, dry_run=False)
    conn.commit()
    return result


def _path_in_raw_dir(root: Path, filename: str) -> Path:
    """Return a raw path only when the database filename is a plain basename."""
    candidate = (root / filename).resolve()
    if candidate.parent != root:
        raise ValueError(f"Raw filename escapes configured directory: {filename}")
    return candidate
