"""Run repeatable bounded replays and print chunk-size benchmark medians.

Usage:
    python -m benchmarks.fact_chunks --zip-path data/sepa.zip \\
      --fecha 2026-09-22 --chain-keys 12:1 --max-product-rows 50000 \\
      --chunk-sizes 5000,10000 --repetitions 3
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path

from benchmarks.summary import summarize_chunk_benchmarks
from etl.development_ingestion import DevelopmentIngestionPolicy, with_cli_overrides
from benchmarks.run_baseline import load_run_baseline
from etl.run_pipeline import _db_connect


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _chunk_sizes(value: str) -> tuple[int, ...]:
    try:
        values = tuple(_positive_int(item.strip()) for item in value.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be comma-separated positive integers") from exc
    if not values:
        raise argparse.ArgumentTypeError("must include at least one chunk size")
    return values


def _environment(conn) -> dict[str, str]:
    with conn.cursor() as cur:
        cur.execute("SHOW server_version")
        postgres_version = cur.fetchone()[0]
    return {
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "postgres_version": postgres_version,
    }


def _latest_run_id(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT COALESCE(MAX(id), 0) FROM ingestion_runs")
        return int(cur.fetchone()[0])


def _run_replay(
    *,
    zip_path: Path,
    fecha: date,
    policy: DevelopmentIngestionPolicy,
    chunk_size: int,
) -> None:
    """Run one sample in a fresh process so its RSS high-water mark is local."""
    command = [
        sys.executable,
        "-m",
        "etl.run_pipeline",
        "--fecha",
        fecha.isoformat(),
        "--zip-path",
        str(zip_path),
        "--fact-chunk-size",
        str(chunk_size),
        "--verbose",
    ]
    for comercio, bandera in policy.chain_keys:
        command.extend(["--dev-chain-key", f"{comercio}:{bandera}"])
    if policy.max_product_rows is not None:
        command.extend(["--dev-max-product-rows", str(policy.max_product_rows)])
    subprocess.run(command, check=True, stdout=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run repeatable, bounded local ETL chunk-size benchmarks."
    )
    parser.add_argument("--zip-path", type=Path, required=True)
    parser.add_argument("--fecha", type=date.fromisoformat, required=True)
    parser.add_argument("--chain-keys", default="")
    parser.add_argument("--max-product-rows", type=_positive_int, default=None)
    parser.add_argument("--chunk-sizes", type=_chunk_sizes, required=True)
    parser.add_argument("--repetitions", type=_positive_int, default=3)
    args = parser.parse_args(argv)

    policy = with_cli_overrides(
        DevelopmentIngestionPolicy(),
        chain_keys=args.chain_keys.split(",") if args.chain_keys else None,
        max_product_rows=args.max_product_rows,
    )
    if not policy.enabled:
        parser.error("set --chain-keys and/or --max-product-rows for an isolated partial run")

    conn = _db_connect()
    try:
        environment = _environment(conn)
    finally:
        conn.close()

    samples = []
    for chunk_size in args.chunk_sizes:
        for _ in range(args.repetitions):
            conn = _db_connect()
            try:
                previous_run_id = _latest_run_id(conn)
            finally:
                conn.close()
            _run_replay(
                zip_path=args.zip_path,
                fecha=args.fecha,
                policy=policy,
                chunk_size=chunk_size,
            )
            conn = _db_connect()
            try:
                run_id = _latest_run_id(conn)
                baseline = load_run_baseline(conn, run_id=run_id)
            finally:
                conn.close()
            if run_id <= previous_run_id:
                print("Benchmark replay did not open a new ingestion run.", file=sys.stderr)
                return 1
            if baseline is None:
                print(f"Completed baseline for run {run_id} was not found.", file=sys.stderr)
                return 1
            if baseline.fact_chunk_size != chunk_size:
                print(
                    f"Newest run {run_id} used an unexpected chunk size.",
                    file=sys.stderr,
                )
                return 1
            samples.append(baseline)

    print(json.dumps({
        "environment": environment,
        "fecha": args.fecha.isoformat(),
        "policy": {
            "chain_keys": [f"{comercio}:{bandera}" for comercio, bandera in policy.chain_keys],
            "max_product_rows": policy.max_product_rows,
        },
        "source_archive": str(args.zip_path.resolve()),
        "samples": [sample.as_dict() for sample in samples],
        "summaries": [asdict(summary) for summary in summarize_chunk_benchmarks(samples)],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
