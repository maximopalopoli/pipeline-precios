"""
downloader.py — Download daily SEPA ZIP and register it in raw_files.

Returns a DownloadResult with is_new=False if the SHA-256 already exists
in the DB (idempotent re-runs). Stores each ZIP under an immutable filename
that includes its SHA-256, preserving rectified versions of the same date.
"""

import hashlib
import os
import tempfile
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import psycopg2
import requests
import structlog

from etl.ingestion_date import resolve_ingestion_date

log = structlog.get_logger(__name__)

# ── Resource IDs (one ZIP per weekday, overwritten weekly by the source) ──────

RESOURCE_IDS: dict[int, str] = {
    0: "0a9069a9-06e8-4f98-874d-da5578693290",  # Monday
    1: "9dc06241-cc83-44f4-8e25-c9b1636b8bc8",  # Tuesday
    2: "1e92cd42-4f94-4071-a165-62c4cb2ce23c",  # Wednesday
    3: "d076720f-a7f0-4af8-b1d6-1b99d5a90c14",  # Thursday
    4: "91bc072a-4726-44a1-85ec-4a8467aad27e",  # Friday
    5: "b3c3da5d-213d-41e7-8d74-f23fda0a3c30",  # Saturday
    6: "f8e75128-515a-436e-bf8d-5c63a62f2005",  # Sunday
}

DAY_NAMES: dict[int, str] = {
    0: "lunes",
    1: "martes",
    2: "miercoles",
    3: "jueves",
    4: "viernes",
    5: "sabado",
    6: "domingo",
}

BASE_URL = (
    "https://datos.produccion.gob.ar/dataset/"
    "6f47ec76-d1ce-4e34-a7e1-621fe9b1d0b5/resource/"
    "{resource_id}/download/sepa_{dia}.zip"
)

# ── Download settings ─────────────────────────────────────────────────────────

TIMEOUT_SECONDS = 300
MAX_RETRIES = 3
RETRY_BACKOFF = [5, 15, 30]  # seconds between retries
CHUNK_SIZE = 1024 * 1024  # 1 MB


# ── Result dataclass ──────────────────────────────────────────────────────────


@dataclass
class DownloadResult:
    raw_file_id: int
    filename: str
    path: Path
    sha256: str
    size_bytes: int
    fecha_datos: date
    is_new: bool


@dataclass
class DownloadPayload:
    path: Path
    sha256: str
    size_bytes: int


# ── Public API ────────────────────────────────────────────────────────────────


def download(
    conn: "psycopg2.connection",
    raw_dir: Path,
    fecha: date | None = None,
) -> DownloadResult:
    """
    Download the SEPA ZIP for *fecha* (defaults to today) and register it.

    Parameters
    ----------
    conn:
        Open psycopg2 connection (caller manages transactions).
    raw_dir:
        Directory where the ZIP will be stored.
    fecha:
        Date to download. Defaults to the current calendar day in Argentina.

    Returns
    -------
    DownloadResult with is_new=False when the file was already downloaded.
    """
    fecha = resolve_ingestion_date(fecha)

    weekday = fecha.weekday()  # 0=Monday … 6=Sunday
    resource_id = RESOURCE_IDS[weekday]
    dia = DAY_NAMES[weekday]
    url = BASE_URL.format(resource_id=resource_id, dia=dia)
    logger = log.bind(fecha=fecha.isoformat(), dia=dia, url=url)
    logger.info("download.start")

    raw_dir.mkdir(parents=True, exist_ok=True)

    # Stream download with retries
    payload = _download_with_retries(url, raw_dir, logger)
    sha256 = payload.sha256
    size_bytes = payload.size_bytes
    filename = f"{fecha.isoformat()}_{dia}_{sha256}.zip"
    dest = raw_dir / filename

    logger = logger.bind(
        sha256=sha256[:12] + "…", size_bytes=size_bytes, filename=filename
    )

    # Check for existing hash before writing anything
    existing_id = _find_by_sha256(conn, sha256)
    if existing_id is not None:
        _update_filename(conn, existing_id, filename)
        if not dest.exists():
            os.replace(payload.path, dest)
            logger.info("download.restored_missing_file", path=str(dest))
        else:
            payload.path.unlink(missing_ok=True)
        logger.info("download.duplicate_skipped", raw_file_id=existing_id)
        return DownloadResult(
            raw_file_id=existing_id,
            filename=filename,
            path=dest,
            sha256=sha256,
            size_bytes=size_bytes,
            fecha_datos=fecha,
            is_new=False,
        )

    os.replace(payload.path, dest)
    logger.info("download.saved", path=str(dest))

    raw_file_id = _register(conn, filename, url, sha256, size_bytes, fecha)
    logger.info("download.registered", raw_file_id=raw_file_id)

    return DownloadResult(
        raw_file_id=raw_file_id,
        filename=filename,
        path=dest,
        sha256=sha256,
        size_bytes=size_bytes,
        fecha_datos=fecha,
        is_new=True,
    )


def register_local_archive(
    conn: "psycopg2.connection", zip_path: Path, fecha: date
) -> DownloadResult:
    """Register an existing ZIP for replay without copying or downloading it.

    The SHA-256 is streamed from disk and remains the idempotency key shared
    with downloaded archives. The pipeline still performs its normal source
    date validation before staging.
    """
    path = zip_path.expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Local source archive does not exist: {path}")

    sha256, size_bytes = hash_file(path)
    filename = path.name
    logger = log.bind(
        fecha=fecha.isoformat(),
        filename=filename,
        path=str(path),
        sha256=sha256[:12] + "…",
        size_bytes=size_bytes,
    )
    existing_id = _find_by_sha256(conn, sha256)
    if existing_id is not None:
        logger.info("source_archive.local_duplicate", raw_file_id=existing_id)
        return DownloadResult(
            raw_file_id=existing_id,
            filename=filename,
            path=path,
            sha256=sha256,
            size_bytes=size_bytes,
            fecha_datos=fecha,
            is_new=False,
        )

    raw_file_id = _register(
        conn, filename, path.as_uri(), sha256, size_bytes, fecha
    )
    logger.info("source_archive.local_registered", raw_file_id=raw_file_id)
    return DownloadResult(
        raw_file_id=raw_file_id,
        filename=filename,
        path=path,
        sha256=sha256,
        size_bytes=size_bytes,
        fecha_datos=fecha,
        is_new=True,
    )


# ── Private helpers ───────────────────────────────────────────────────────────


def _download_with_retries(
    url: str, raw_dir: Path, logger: structlog.BoundLogger
) -> DownloadPayload:
    last_exc: Exception | None = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.info("download.attempt", attempt=attempt)
            return _stream_download(url, raw_dir)
        except requests.RequestException as exc:
            last_exc = exc
            logger.warning(
                "download.retry",
                attempt=attempt,
                error=str(exc),
            )
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF[attempt - 1])

    raise RuntimeError(
        f"Download failed after {MAX_RETRIES} attempts: {last_exc}"
    ) from last_exc


def _stream_download(url: str, raw_dir: Path) -> DownloadPayload:
    """Stream a response directly to a temporary raw-file payload."""
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=raw_dir, suffix=".part", delete=False) as temp:
            temp_path = Path(temp.name)
            digest = hashlib.sha256()
            size_bytes = 0
            with requests.get(url, stream=True, timeout=TIMEOUT_SECONDS) as resp:
                resp.raise_for_status()
                for chunk in resp.iter_content(chunk_size=CHUNK_SIZE):
                    if chunk:
                        temp.write(chunk)
                        digest.update(chunk)
                        size_bytes += len(chunk)
        return DownloadPayload(
            path=temp_path, sha256=digest.hexdigest(), size_bytes=size_bytes
        )
    except Exception:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        raise


def hash_file(path: Path) -> tuple[str, int]:
    """Return a file's SHA-256 and byte size without loading it into memory."""
    digest = hashlib.sha256()
    size_bytes = 0
    with path.open("rb") as source:
        while chunk := source.read(CHUNK_SIZE):
            digest.update(chunk)
            size_bytes += len(chunk)
    return digest.hexdigest(), size_bytes


def _find_by_sha256(conn: "psycopg2.connection", sha256: str) -> int | None:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM raw_files WHERE sha256 = %s", (sha256,))
        row = cur.fetchone()
    return row[0] if row else None


def _register(
    conn: "psycopg2.connection",
    filename: str,
    url: str,
    sha256: str,
    size_bytes: int,
    fecha_datos: date,
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO raw_files (filename, url, sha256, size_bytes, fecha_datos)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (sha256) DO NOTHING
            RETURNING id
            """,
            (filename, url, sha256, size_bytes, fecha_datos),
        )
        row = cur.fetchone()
        if row is None:
            # Race condition: another process inserted the same hash between our
            # SELECT and this INSERT. Fetch the existing id.
            cur.execute("SELECT id FROM raw_files WHERE sha256 = %s", (sha256,))
            row = cur.fetchone()
        conn.commit()
    return row[0]


def _update_filename(conn: "psycopg2.connection", raw_file_id: int, filename: str) -> None:
    """Associate a duplicate hash with its canonical immutable filename."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE raw_files SET filename = %s WHERE id = %s",
            (filename, raw_file_id),
        )
    conn.commit()
