import pytest

from etl.fact_chunking import from_environment, with_cli_override
from etl.transform.fact_builder import PRODUTO_CHUNK_SIZE


def test_fact_chunk_size_uses_the_stable_default_when_unset():
    assert from_environment({}) == PRODUTO_CHUNK_SIZE


@pytest.mark.parametrize("raw", ["0", "-1", "many"])
def test_fact_chunk_size_rejects_invalid_environment_values(raw):
    with pytest.raises(ValueError, match="SEPA_FACT_CHUNK_SIZE"):
        from_environment({"SEPA_FACT_CHUNK_SIZE": raw})


def test_fact_chunk_size_cli_override_wins_over_environment():
    assert with_cli_override(
        chunk_size=50_000, env={"SEPA_FACT_CHUNK_SIZE": "100000"}
    ) == 50_000
