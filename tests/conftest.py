"""
Shared pytest fixtures.
"""

import pathlib
import pytest
from pytest_postgresql import factories

MIGRATIONS_DIR = pathlib.Path(__file__).parent.parent / "db" / "migrations"
FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures"

# Spin up a temporary PostgreSQL instance for the test session
postgresql_proc = factories.postgresql_proc()
postgresql = factories.postgresql("postgresql_proc")


@pytest.fixture()
def pg_conn(postgresql):
    """
    psycopg2 connection to the test database with all migrations applied.
    """
    conn = postgresql
    with conn.cursor() as cur:
        for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
            cur.execute(migration.read_text())
    conn.commit()
    yield conn


@pytest.fixture()
def sample_zip_path() -> pathlib.Path:
    return FIXTURES_DIR / "sample.zip"
