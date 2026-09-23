"""Pure selection behavior for bounded staging loads."""

from etl.staging.loader import _matches_chain_key


def test_chain_match_uses_both_source_identifiers():
    assert _matches_chain_key(["10", "2"], 0, 1, ((10, 2),)) is True
    assert _matches_chain_key(["10", "3"], 0, 1, ((10, 2),)) is False


def test_chain_match_rejects_missing_or_malformed_source_identifiers():
    assert _matches_chain_key(["10"], 0, 1, ((10, 2),)) is False
    assert _matches_chain_key(["ten", "2"], 0, 1, ((10, 2),)) is False
    assert _matches_chain_key(["10", "2"], None, 1, ((10, 2),)) is False
