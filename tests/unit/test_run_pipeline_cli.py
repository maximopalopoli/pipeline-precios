"""CLI wiring for explicit bounded development ingestion options."""

import sys
from datetime import date
from pathlib import Path

import pytest

import etl.run_pipeline as pipeline
from etl.ingestion_run import IngestionResult


def test_cli_passes_explicit_development_bounds_to_the_pipeline(monkeypatch):
    captured = {}
    monkeypatch.setattr(pipeline, "_configure_logging", lambda: None)
    monkeypatch.setattr(
        pipeline,
        "run",
        lambda **kwargs: captured.update(kwargs) or IngestionResult.completed(0),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_pipeline",
            "--fecha",
            "2026-03-17",
            "--raw-dir",
            "local-data",
            "--dev-chain-key",
            "40:2",
            "--dev-chain-key",
            "10:3",
            "--dev-max-product-rows",
            "5000",
            "--fact-chunk-size",
            "50000",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        pipeline.main()

    assert exc_info.value.code == 0
    assert captured["fecha"] == date(2026, 3, 17)
    assert captured["raw_dir"] == Path("local-data")
    assert captured["development_policy"].chain_keys == ((10, 3), (40, 2))
    assert captured["development_policy"].max_product_rows == 5000
    assert captured["fact_chunk_size"] == 50000


def test_cli_rejects_a_nonpositive_product_bound(monkeypatch):
    monkeypatch.setattr(pipeline, "_configure_logging", lambda: None)
    monkeypatch.setattr(sys, "argv", ["run_pipeline", "--dev-max-product-rows", "0"])

    with pytest.raises(SystemExit) as exc_info:
        pipeline.main()

    assert exc_info.value.code == 1


def test_cli_rejects_a_nonpositive_fact_chunk_size(monkeypatch):
    monkeypatch.setattr(pipeline, "_configure_logging", lambda: None)
    monkeypatch.setattr(sys, "argv", ["run_pipeline", "--fact-chunk-size", "0"])

    with pytest.raises(SystemExit) as exc_info:
        pipeline.main()

    assert exc_info.value.code == 1
