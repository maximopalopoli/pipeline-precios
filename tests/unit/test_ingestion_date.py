from datetime import date, datetime, timezone

import pytest

from etl.ingestion_date import ARGENTINA_TIMEZONE, resolve_ingestion_date


def test_explicit_ingestion_date_is_authoritative():
    requested = date(2026, 3, 15)

    assert resolve_ingestion_date(requested) == requested


def test_default_date_uses_argentina_calendar_day_at_utc_boundary():
    utc_time = datetime(2026, 3, 16, 2, 30, tzinfo=timezone.utc)

    assert resolve_ingestion_date(now=utc_time) == date(2026, 3, 15)


def test_default_date_converts_other_timezones_to_argentina():
    madrid_time = datetime(2026, 3, 16, 3, 30, tzinfo=timezone.utc)

    assert resolve_ingestion_date(now=madrid_time) == date(2026, 3, 16)
    assert ARGENTINA_TIMEZONE.key == "America/Argentina/Buenos_Aires"


def test_default_date_rejects_ambiguous_naive_time():
    with pytest.raises(ValueError, match="timezone"):
        resolve_ingestion_date(now=datetime(2026, 3, 16, 0, 30))
