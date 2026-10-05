"""The ingestion DAG runs on Argentine time and ingests its scheduled day.

Airflow is not installed in the test environment, so the DAG is read as source.
"""

import ast
from pathlib import Path

from etl.ingestion_date import ARGENTINA_TIMEZONE

DAG_FILE = Path(__file__).resolve().parents[2] / "dags" / "dag_sepa_ingest.py"


def _dag_call() -> ast.Call:
    tree = ast.parse(DAG_FILE.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.With):
            call = node.items[0].context_expr
            if isinstance(call, ast.Call) and getattr(call.func, "id", None) == "DAG":
                return call
    raise AssertionError("DAG definition not found")


def _keyword(call: ast.Call, name: str) -> ast.expr:
    return next(k.value for k in call.keywords if k.arg == name)


def test_runs_at_0630_and_1030():
    assert ast.literal_eval(_keyword(_dag_call(), "schedule")) == "30 6,10 * * *"


def test_start_date_carries_the_argentine_time_zone():
    start_date = _keyword(_dag_call(), "start_date")

    assert ast.unparse(start_date) == (
        "pendulum.datetime(2026, 3, 19, tz=ARGENTINA_TIMEZONE.key)"
    )
    assert ARGENTINA_TIMEZONE.key == "America/Argentina/Buenos_Aires"


def test_download_dates_the_run_by_its_scheduled_moment_not_ds():
    tree = ast.parse(DAG_FILE.read_text())
    download = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "download_zip"
    )

    assert [arg.arg for arg in download.args.args] == ["data_interval_end"]
    assert "ds" not in {
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
    }
