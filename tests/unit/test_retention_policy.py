"""Behavior of opt-in local fact-retention settings."""

from datetime import date

import pytest

from etl.retention_policy import RetentionPolicy, from_environment


def test_retention_is_disabled_without_an_explicit_setting():
    policy = from_environment({})

    assert policy.fact_retention_enabled is False
    assert policy.fact_cutoff(date(2026, 3, 31)) is None


def test_retention_window_calculates_the_oldest_detail_date_to_keep():
    policy = from_environment(
        {
            "SEPA_FACT_RETENTION_DAYS": "90",
            "SEPA_STAGING_RETENTION_DAYS": "7",
            "SEPA_RAW_RETENTION_DAYS": "30",
        }
    )

    assert policy == RetentionPolicy(
        fact_retention_days=90, staging_retention_days=7, raw_retention_days=30
    )
    assert policy.fact_cutoff(date(2026, 3, 31)) == date(2025, 12, 31)
    assert policy.staging_cutoff(date(2026, 3, 31)) == date(2026, 3, 24)
    assert policy.raw_cutoff(date(2026, 3, 31)) == date(2026, 3, 1)


@pytest.mark.parametrize("value", ["0", "-1", "thirty", "90.5"])
def test_invalid_retention_setting_fails_without_choosing_a_default(value):
    with pytest.raises(ValueError, match="SEPA_FACT_RETENTION_DAYS"):
        from_environment({"SEPA_FACT_RETENTION_DAYS": value})


def test_blank_retention_setting_is_treated_as_disabled():
    assert from_environment({"SEPA_FACT_RETENTION_DAYS": "  "}) == RetentionPolicy()


def test_invalid_staging_retention_setting_fails_without_a_default():
    with pytest.raises(ValueError, match="SEPA_STAGING_RETENTION_DAYS"):
        from_environment({"SEPA_STAGING_RETENTION_DAYS": "-7"})


def test_invalid_raw_retention_setting_fails_without_a_default():
    with pytest.raises(ValueError, match="SEPA_RAW_RETENTION_DAYS"):
        from_environment({"SEPA_RAW_RETENTION_DAYS": "zero"})
