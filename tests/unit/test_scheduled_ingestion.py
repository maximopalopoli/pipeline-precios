import configparser
import os
import stat
import subprocess
from datetime import datetime
from pathlib import Path

from etl.ingestion_date import ARGENTINA_TIMEZONE

ROOT = Path(__file__).resolve().parents[2]
WRAPPER = ROOT / "deploy" / "run-ingest.sh"
TIMER = ROOT / "deploy" / "systemd" / "sepa-ingest.timer"


def _fake_python(tmp_path: Path, failures: int) -> Path:
    """Record each call and fail the first ``failures`` of them."""
    script = tmp_path / "python"
    script.write_text(
        f"""#!/bin/sh
echo "$* dev=${{SEPA_DEV_CHAIN_KEYS:-}}${{SEPA_DEV_MAX_PRODUCT_ROWS:-}}" >> {tmp_path}/calls
[ "$(wc -l < {tmp_path}/calls)" -gt {failures} ]
"""
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def _run(tmp_path: Path, failures: int) -> tuple[subprocess.CompletedProcess, list[str]]:
    env = {
        **os.environ,
        "INGEST_PYTHON": str(_fake_python(tmp_path, failures)),
        "INGEST_RETRY_DELAY_SECONDS": "0",
        "SEPA_DEV_CHAIN_KEYS": "12:1",
        "SEPA_DEV_MAX_PRODUCT_ROWS": "5000",
    }
    result = subprocess.run(["sh", str(WRAPPER)], env=env, capture_output=True, text=True)
    return result, (tmp_path / "calls").read_text().splitlines()


def test_scheduled_ingestion_retries_the_same_argentine_day(tmp_path):
    result, calls = _run(tmp_path, failures=2)

    today = datetime.now(ARGENTINA_TIMEZONE).date().isoformat()
    assert result.returncode == 0
    assert calls == [f"-m etl.run_pipeline --fecha {today} dev="] * 3


def test_scheduled_ingestion_fails_after_its_last_attempt(tmp_path):
    result, calls = _run(tmp_path, failures=5)

    assert result.returncode == 1
    assert len(calls) == 3
    assert "failed after 3 attempts" in result.stderr


def test_timer_runs_at_both_argentine_slots():
    timer = configparser.ConfigParser()
    timer.read(TIMER)

    assert timer["Timer"]["OnCalendar"] == (
        "*-*-* 06,10:30:00 America/Argentina/Buenos_Aires"
    )
    assert timer["Timer"]["Persistent"] == "true"
