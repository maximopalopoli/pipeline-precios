"""The scheduled ingestion must see the same production settings as the CLI."""

from pathlib import Path
import re

import yaml

ROOT = Path(__file__).parent.parent.parent
# Development bounds must never reach the scheduler; the archive folder is a
# host path that maps to data/archive inside the containers.
NOT_FOR_AIRFLOW = {"SEPA_DEV_CHAIN_KEYS", "SEPA_DEV_MAX_PRODUCT_ROWS", "SEPA_ARCHIVE_DIR"}
# Read only by the CLI's chunk commits.
CLI_ONLY = {"SEPA_PENDING_FACT_COMMIT_EVERY_CHUNKS"}


def _airflow_environment() -> dict[str, str]:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    return compose["x-airflow-common"]["environment"]


def _settings_read_by_the_etl() -> set[str]:
    names = set()
    for path in (ROOT / "etl").rglob("*.py"):
        names |= set(re.findall(r'"((?:SEPA|ALERT|SMTP)_[A-Z_]+)"', path.read_text()))
    return names


def test_airflow_receives_every_production_setting():
    expected = _settings_read_by_the_etl() - NOT_FOR_AIRFLOW - CLI_ONLY
    environment = _airflow_environment()
    assert expected <= environment.keys()
    for name in expected:
        assert environment[name] == f"${{{name}:-}}"


def test_airflow_never_receives_development_bounds():
    assert not NOT_FOR_AIRFLOW & _airflow_environment().keys()
