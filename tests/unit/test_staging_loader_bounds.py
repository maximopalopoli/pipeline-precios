"""Pure selection behavior for bounded staging loads."""

from etl.staging.loader import _inner_zip_matches_chain_key, _matches_chain_key


def test_chain_match_uses_both_source_identifiers():
    assert _matches_chain_key(["10", "2"], 0, 1, ((10, 2),)) is True
    assert _matches_chain_key(["10", "3"], 0, 1, ((10, 2),)) is False


def test_chain_match_rejects_missing_or_malformed_source_identifiers():
    assert _matches_chain_key(["10"], 0, 1, ((10, 2),)) is False
    assert _matches_chain_key(["ten", "2"], 0, 1, ((10, 2),)) is False
    assert _matches_chain_key(["10", "2"], None, 1, ((10, 2),)) is False


def test_inner_zip_selection_uses_the_chain_key_encoded_by_sepa():
    assert _inner_zip_matches_chain_key(
        "2026-09-22/sepa_1_comercio-sepa-12_2026-09-22_09-05-11.zip",
        ((12, 1),),
    )
    assert not _inner_zip_matches_chain_key(
        "2026-09-22/sepa_2_comercio-sepa-36_2026-09-22_01-05-08.zip",
        ((12, 1),),
    )


def test_inner_zip_selection_keeps_unknown_source_names():
    assert _inner_zip_matches_chain_key("nested/archive.zip", ((12, 1),))
