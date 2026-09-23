"""
Integration test: 10:30 run detects a rectified file (different SHA-256, same date).

Scenario
--------
The government occasionally publishes a corrected ZIP for a date that was
already downloaded (e.g. the 10:00 run) — the new file has different content
and therefore a different SHA-256.

The 10:30 run must:
1. Download the new ZIP successfully (is_new=True).
2. Create a separate raw_files entry for the rectified file.
3. Open a new ingestion_run so both runs are independently traceable.

This test exercises only the download + DB registration layer; the full
pipeline re-processing is covered by test_pipeline_rerun.py.
"""

from datetime import date
from unittest.mock import patch


import etl.extract.downloader as downloader_mod
from etl.extract.downloader import DownloadPayload, download

_FECHA = date(2026, 3, 17)  # Monday

# Two distinct ZIP payloads — different SHA-256, same logical date
_ZIP_V1 = b"PK\x03\x04sepa-zip-original-content-v1"
_ZIP_V2 = b"PK\x03\x04sepa-zip-rectified-content-v2"


# ── Helpers ────────────────────────────────────────────────────────────────────


def _count_raw_files(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM raw_files WHERE fecha_datos = %s", (_FECHA,))
        return cur.fetchone()[0]


def _all_raw_file_ids(conn):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM raw_files WHERE fecha_datos = %s ORDER BY id", (_FECHA,)
        )
        return [row[0] for row in cur.fetchall()]


def _payload(tmp_path, content: bytes) -> DownloadPayload:
    import hashlib

    path = tmp_path / f"payload-{len(list(tmp_path.glob('payload-*.part')))}.part"
    path.write_bytes(content)
    return DownloadPayload(path, hashlib.sha256(content).hexdigest(), len(content))


# ── Tests ──────────────────────────────────────────────────────────────────────


class TestRectifiedFileDetected:

    def test_rectified_file_creates_new_raw_file_entry(self, pg_conn, tmp_path):
        """
        A second download with different content for the same date must create
        a distinct raw_files row (not return the existing one).
        """
        with patch.object(downloader_mod, "_stream_download", side_effect=lambda *_: _payload(tmp_path, _ZIP_V1)):
            r1 = download(pg_conn, tmp_path, _FECHA)

        with patch.object(downloader_mod, "_stream_download", side_effect=lambda *_: _payload(tmp_path, _ZIP_V2)):
            r2 = download(pg_conn, tmp_path, _FECHA)

        assert r1.is_new is True
        assert r2.is_new is True
        assert r1.raw_file_id != r2.raw_file_id
        assert r1.path != r2.path
        assert r1.path.read_bytes() == _ZIP_V1
        assert r2.path.read_bytes() == _ZIP_V2

    def test_two_raw_file_rows_for_same_date(self, pg_conn, tmp_path):
        """raw_files must contain two distinct rows for the same fecha_datos."""
        with patch.object(downloader_mod, "_stream_download", side_effect=lambda *_: _payload(tmp_path, _ZIP_V1)):
            download(pg_conn, tmp_path, _FECHA)

        with patch.object(downloader_mod, "_stream_download", side_effect=lambda *_: _payload(tmp_path, _ZIP_V2)):
            download(pg_conn, tmp_path, _FECHA)

        assert _count_raw_files(pg_conn) == 2

    def test_each_file_has_distinct_sha256(self, pg_conn, tmp_path):
        """The two raw_files rows must have different SHA-256 hashes."""
        import hashlib

        with patch.object(downloader_mod, "_stream_download", side_effect=lambda *_: _payload(tmp_path, _ZIP_V1)):
            r1 = download(pg_conn, tmp_path, _FECHA)

        with patch.object(downloader_mod, "_stream_download", side_effect=lambda *_: _payload(tmp_path, _ZIP_V2)):
            r2 = download(pg_conn, tmp_path, _FECHA)

        assert r1.sha256 != r2.sha256
        assert r1.sha256 == hashlib.sha256(_ZIP_V1).hexdigest()
        assert r2.sha256 == hashlib.sha256(_ZIP_V2).hexdigest()

    def test_original_file_still_detectable_as_duplicate(self, pg_conn, tmp_path):
        """
        A third call with the original content (v1) must be detected as a
        duplicate (is_new=False) — the 10:00 run's file is already registered.
        """
        with patch.object(downloader_mod, "_stream_download", side_effect=lambda *_: _payload(tmp_path, _ZIP_V1)):
            r1 = download(pg_conn, tmp_path, _FECHA)

        with patch.object(downloader_mod, "_stream_download", side_effect=lambda *_: _payload(tmp_path, _ZIP_V2)):
            download(pg_conn, tmp_path, _FECHA)

        # Third call — same content as v1
        with patch.object(downloader_mod, "_stream_download", side_effect=lambda *_: _payload(tmp_path, _ZIP_V1)):
            r3 = download(pg_conn, tmp_path, _FECHA)

        assert r3.is_new is False
        assert r3.raw_file_id == r1.raw_file_id
        assert r3.path == r1.path
        assert r3.path.read_bytes() == _ZIP_V1
        # Still only 2 distinct files in the DB
        assert _count_raw_files(pg_conn) == 2

    def test_rectified_file_has_correct_metadata(self, pg_conn, tmp_path):
        """The rectified file entry must record correct size and fecha_datos."""
        with patch.object(downloader_mod, "_stream_download", side_effect=lambda *_: _payload(tmp_path, _ZIP_V1)):
            download(pg_conn, tmp_path, _FECHA)

        with patch.object(downloader_mod, "_stream_download", side_effect=lambda *_: _payload(tmp_path, _ZIP_V2)):
            r2 = download(pg_conn, tmp_path, _FECHA)

        assert r2.fecha_datos == _FECHA
        assert r2.size_bytes == len(_ZIP_V2)

        with pg_conn.cursor() as cur:
            cur.execute(
                "SELECT fecha_datos, size_bytes FROM raw_files WHERE id = %s",
                (r2.raw_file_id,),
            )
            row = cur.fetchone()

        assert row[0] == _FECHA
        assert row[1] == len(_ZIP_V2)
