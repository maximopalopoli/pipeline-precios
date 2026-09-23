"""Validation of the explicit local bounded-ingestion configuration."""

import pytest

from etl.development_ingestion import (
    DevelopmentIngestionPolicy,
    from_environment,
    with_cli_overrides,
)


def test_development_ingestion_is_disabled_without_explicit_bounds():
    assert from_environment({}) == DevelopmentIngestionPolicy()
    assert from_environment({}).enabled is False


def test_development_ingestion_parses_deterministic_chain_keys_and_row_cap():
    policy = from_environment(
        {
            "SEPA_DEV_CHAIN_KEYS": "40:2, 10:3,40:2",
            "SEPA_DEV_MAX_PRODUCT_ROWS": "5000",
        }
    )

    assert policy == DevelopmentIngestionPolicy(
        chain_keys=((10, 3), (40, 2)), max_product_rows=5000
    )
    assert policy.enabled is True


@pytest.mark.parametrize(
    "value",
    ["1", "1:2:3", "one:2", "1:zero", "0:2", "1:-2", ","],
)
def test_invalid_development_chain_keys_fail_fast(value):
    with pytest.raises(ValueError, match="SEPA_DEV_CHAIN_KEYS"):
        from_environment({"SEPA_DEV_CHAIN_KEYS": value})


@pytest.mark.parametrize("value", ["0", "-1", "many", "10.5"])
def test_invalid_development_product_cap_fails_fast(value):
    with pytest.raises(ValueError, match="SEPA_DEV_MAX_PRODUCT_ROWS"):
        from_environment({"SEPA_DEV_MAX_PRODUCT_ROWS": value})


def test_blank_development_bounds_are_disabled():
    assert from_environment(
        {"SEPA_DEV_CHAIN_KEYS": "  ", "SEPA_DEV_MAX_PRODUCT_ROWS": "  "}
    ) == DevelopmentIngestionPolicy()


def test_cli_bounds_override_only_the_environment_bound_that_was_supplied():
    environment_policy = from_environment(
        {"SEPA_DEV_CHAIN_KEYS": "1:1", "SEPA_DEV_MAX_PRODUCT_ROWS": "100"}
    )

    policy = with_cli_overrides(environment_policy, chain_keys=["40:2", "10:3"])

    assert policy == DevelopmentIngestionPolicy(
        chain_keys=((10, 3), (40, 2)), max_product_rows=100
    )
