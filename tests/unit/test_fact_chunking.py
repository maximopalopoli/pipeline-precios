import pytest

from etl.fact_chunking import from_environment, from_run, with_cli_override
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


class _Cursor:
    def __init__(self, row):
        self.row = row

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def execute(self, sql, params):
        self.params = params

    def fetchone(self):
        return self.row


class _Connection:
    def __init__(self, row):
        self.cursor_instance = _Cursor(row)

    def cursor(self):
        return self.cursor_instance


def test_fact_chunk_size_reads_the_persisted_run_value():
    conn = _Connection((50_000,))

    assert from_run(conn, 7) == 50_000
    assert conn.cursor_instance.params == (7,)


def test_fact_chunk_size_uses_default_for_runs_created_before_the_column():
    assert from_run(_Connection((None,)), 7) == PRODUTO_CHUNK_SIZE
