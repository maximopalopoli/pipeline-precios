from datetime import date, datetime, timezone

import pytest

from etl.ingestion_date import (
    ARGENTINA_TIMEZONE,
    resolve_ingestion_date,
    scheduled_ingestion_date,
)


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


@pytest.mark.parametrize("hour", [6, 10])
def test_both_scheduled_runs_ingest_the_argentine_day_they_run_on(hour):
    scheduled_for = datetime(2026, 3, 17, hour, 30, tzinfo=ARGENTINA_TIMEZONE)

    assert scheduled_ingestion_date(scheduled_for) == date(2026, 3, 17)


def test_scheduled_date_reads_a_utc_interval_end_in_argentina():
    # Airflow passes data_interval_end in UTC: 06:30 in Argentina is 09:30 UTC.
    assert scheduled_ingestion_date(
        datetime(2026, 3, 17, 9, 30, tzinfo=timezone.utc)
    ) == date(2026, 3, 17)


def test_the_interval_start_of_the_first_run_would_name_the_previous_day():
    # ds of the 06:30 run is the previous 10:30 slot; the DAG must not use it.
    interval_start = datetime(2026, 3, 16, 10, 30, tzinfo=ARGENTINA_TIMEZONE)

    assert scheduled_ingestion_date(interval_start) == date(2026, 3, 16)
