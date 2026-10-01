import pytest

from etl.quality_thresholds import QualityThresholds, thresholds_from_environment


def test_unset_variables_keep_the_defaults():
    assert thresholds_from_environment({}) == QualityThresholds()
    assert thresholds_from_environment({"SEPA_MAX_REJECTED_SHARE": " "}) == QualityThresholds()


def test_variables_override_each_limit():
    assert thresholds_from_environment(
        {
            "SEPA_MIN_PRODUCT_ROWS": "5000000",
            "SEPA_MAX_ROW_COUNT_DRIFT": "0.3",
            "SEPA_MAX_REJECTED_SHARE": "0.002",
            "SEPA_MAX_WITHOUT_KEYS_SHARE": "0",
            "SEPA_MAX_STORE_UNLISTED_SHARE": "1",
        }
    ) == QualityThresholds(
        min_product_rows=5_000_000,
        max_row_count_drift=0.3,
        max_rejected_share=0.002,
        max_without_keys_share=0.0,
        max_store_unlisted_share=1.0,
    )


@pytest.mark.parametrize(
    "variable, value, message",
    [
        ("SEPA_MIN_PRODUCT_ROWS", "1.5", "a whole number"),
        ("SEPA_MIN_PRODUCT_ROWS", "-1", "zero or positive"),
        ("SEPA_MAX_REJECTED_SHARE", "1%", "a number"),
        ("SEPA_MAX_WITHOUT_KEYS_SHARE", "1.5", "between 0 and 1"),
        ("SEPA_MAX_ROW_COUNT_DRIFT", "-0.1", "between 0 and 1"),
    ],
)
def test_invalid_values_are_rejected(variable, value, message):
    with pytest.raises(ValueError, match=f"{variable} must be {message}"):
        thresholds_from_environment({variable: value})
