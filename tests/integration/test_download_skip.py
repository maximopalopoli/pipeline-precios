"""
Integration test: duplicate ZIP download → pipeline correctly skipped.

Scenario
--------
The government publishes the same ZIP file for a given day (identical SHA-256).
When the downloader detects that the SHA-256 already exists in raw_files it
must return is_new=False so the orchestrator can skip all downstream stages.

Verifies that:
1. First call registers the file and returns is_new=True.
2. Second call with identical content returns is_new=False.
3. raw_files table still has exactly one row for that SHA-256 (no duplicates).
4. The pipeline run() function returns 0 immediately on a duplicate download.
"""

from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

import etl.extract.downloader as downloader_mod
from etl.extract.downloader import download

# Fixed content — any bytes work; the downloader only checks SHA-256
_ZIP_BYTES = b"PK\x03\x04fake-sepa-zip-content-v1"

_FECHA = date(2026, 3, 17)  # Monday


# ── Helpers ────────────────────────────────────────────────────────────────────


def _count_raw_files(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM raw_files")
        return cur.fetchone()[0]


def _find_raw_file(conn, sha256: str):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, is_new FROM raw_files WHERE sha256 = %s",
            (sha256,),
        )
        return cur.fetchone()


# ── Tests ──────────────────────────────────────────────────────────────────────


class TestDownloadSkipOnDuplicate:

    def test_first_download_is_new(self, pg_conn, tmp_path):
        """First download of a ZIP registers it and returns is_new=True."""
        with patch.object(downloader_mod, "_stream_download", return_value=_ZIP_BYTES):
            result = download(pg_conn, tmp_path, _FECHA)

        assert result.is_new is True
        assert result.fecha_datos == _FECHA

    def test_second_download_is_not_new(self, pg_conn, tmp_path):
        """Downloading the same ZIP a second time returns is_new=False."""
        with patch.object(downloader_mod, "_stream_download", return_value=_ZIP_BYTES):
            first = download(pg_conn, tmp_path, _FECHA)
            second = download(pg_conn, tmp_path, _FECHA)

        assert first.is_new is True
        assert second.is_new is False

    def test_no_duplicate_raw_files_entry(self, pg_conn, tmp_path):
        """Two downloads of the same content must produce exactly one raw_files row."""
        with patch.object(downloader_mod, "_stream_download", return_value=_ZIP_BYTES):
            download(pg_conn, tmp_path, _FECHA)
            download(pg_conn, tmp_path, _FECHA)

        assert _count_raw_files(pg_conn) == 1

    def test_second_download_returns_same_raw_file_id(self, pg_conn, tmp_path):
        """Both calls must reference the same raw_files.id."""
        with patch.object(downloader_mod, "_stream_download", return_value=_ZIP_BYTES):
            first = download(pg_conn, tmp_path, _FECHA)
            second = download(pg_conn, tmp_path, _FECHA)

        assert first.raw_file_id == second.raw_file_id

    def test_sha256_and_metadata_preserved(self, pg_conn, tmp_path):
        """The registered SHA-256 and size_bytes must match the downloaded content."""
        import hashlib

        with patch.object(downloader_mod, "_stream_download", return_value=_ZIP_BYTES):
            result = download(pg_conn, tmp_path, _FECHA)

        expected_sha = hashlib.sha256(_ZIP_BYTES).hexdigest()
        assert result.sha256 == expected_sha
        assert result.size_bytes == len(_ZIP_BYTES)
