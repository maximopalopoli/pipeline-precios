"""
Unit tests: downloader retry logic on network failures.

Verifies that _download_with_retries():
  - Retries up to MAX_RETRIES times on requests.RequestException.
  - Returns bytes on the first successful attempt.
  - Raises RuntimeError after exhausting all retries.
  - Does not sleep after the last failed attempt.
"""

from unittest.mock import MagicMock, call, patch

import pytest
import requests

import etl.extract.downloader as downloader_mod
from etl.extract.downloader import MAX_RETRIES, RETRY_BACKOFF, _download_with_retries


# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_logger():
    """Minimal structlog-compatible logger stub."""
    logger = MagicMock()
    logger.bind.return_value = logger
    return logger


# ── Tests ──────────────────────────────────────────────────────────────────────


class TestDownloadWithRetries:

    def test_succeeds_on_first_attempt(self):
        """No retries needed — returns bytes immediately."""
        expected = b"zip-content"
        logger = _make_logger()

        with patch.object(downloader_mod, "_stream_download", return_value=expected) as mock_dl:
            result = _download_with_retries("http://example.com/file.zip", logger)

        assert result == expected
        assert mock_dl.call_count == 1

    def test_retries_once_then_succeeds(self):
        """First attempt fails, second succeeds → returns bytes after 1 retry."""
        expected = b"zip-ok"
        logger = _make_logger()

        side_effects = [requests.ConnectionError("timeout"), expected]

        with (
            patch.object(downloader_mod, "_stream_download", side_effect=side_effects),
            patch.object(downloader_mod.time, "sleep") as mock_sleep,
        ):
            result = _download_with_retries("http://example.com/file.zip", logger)

        assert result == expected
        # Should have slept once with the first backoff interval
        mock_sleep.assert_called_once_with(RETRY_BACKOFF[0])

    def test_retries_twice_then_succeeds(self):
        """Two failures then success → sleeps with correct backoff values."""
        expected = b"zip-ok"
        logger = _make_logger()

        side_effects = [
            requests.ConnectionError("err1"),
            requests.ConnectionError("err2"),
            expected,
        ]

        with (
            patch.object(downloader_mod, "_stream_download", side_effect=side_effects),
            patch.object(downloader_mod.time, "sleep") as mock_sleep,
        ):
            result = _download_with_retries("http://example.com/file.zip", logger)

        assert result == expected
        assert mock_sleep.call_count == 2
        assert mock_sleep.call_args_list == [call(RETRY_BACKOFF[0]), call(RETRY_BACKOFF[1])]

    def test_raises_after_max_retries_exhausted(self):
        """All MAX_RETRIES attempts fail → RuntimeError is raised."""
        logger = _make_logger()

        with (
            patch.object(
                downloader_mod,
                "_stream_download",
                side_effect=requests.ConnectionError("network down"),
            ),
            patch.object(downloader_mod.time, "sleep"),
        ):
            with pytest.raises(RuntimeError, match=f"after {MAX_RETRIES} attempts"):
                _download_with_retries("http://example.com/file.zip", logger)

    def test_attempt_count_equals_max_retries(self):
        """_stream_download is called exactly MAX_RETRIES times before giving up."""
        logger = _make_logger()

        with (
            patch.object(
                downloader_mod,
                "_stream_download",
                side_effect=requests.ConnectionError("fail"),
            ) as mock_dl,
            patch.object(downloader_mod.time, "sleep"),
        ):
            with pytest.raises(RuntimeError):
                _download_with_retries("http://example.com/file.zip", logger)

        assert mock_dl.call_count == MAX_RETRIES

    def test_no_sleep_after_last_failed_attempt(self):
        """sleep must not be called after the final failed attempt."""
        logger = _make_logger()

        with (
            patch.object(
                downloader_mod,
                "_stream_download",
                side_effect=requests.ConnectionError("fail"),
            ),
            patch.object(downloader_mod.time, "sleep") as mock_sleep,
        ):
            with pytest.raises(RuntimeError):
                _download_with_retries("http://example.com/file.zip", logger)

        # With MAX_RETRIES=3 there are 2 intermediate sleeps (after attempt 1 and 2)
        assert mock_sleep.call_count == MAX_RETRIES - 1

    def test_http_error_triggers_retry(self):
        """HTTPError (e.g. 503) is a requests.RequestException and must be retried."""
        logger = _make_logger()

        http_error = requests.HTTPError(response=MagicMock(status_code=503))
        side_effects = [http_error, b"ok"]

        with (
            patch.object(downloader_mod, "_stream_download", side_effect=side_effects),
            patch.object(downloader_mod.time, "sleep"),
        ):
            result = _download_with_retries("http://example.com/file.zip", logger)

        assert result == b"ok"

    def test_original_exception_chained_in_runtime_error(self):
        """RuntimeError.__cause__ must be the last RequestException."""
        logger = _make_logger()
        original = requests.ConnectionError("root cause")

        with (
            patch.object(
                downloader_mod,
                "_stream_download",
                side_effect=original,
            ),
            patch.object(downloader_mod.time, "sleep"),
        ):
            with pytest.raises(RuntimeError) as exc_info:
                _download_with_retries("http://example.com/file.zip", logger)

        assert exc_info.value.__cause__ is original
