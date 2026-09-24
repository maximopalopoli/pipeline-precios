#!/usr/bin/env python3
"""Measure a staging-only load without leaving an ingestion run behind."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from etl.performance import process_max_rss_bytes
from etl.run_pipeline import _db_connect, _configure_logging
from etl.staging import loader


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip-path", type=Path, required=True)
    parser.add_argument("--fecha", type=date.fromisoformat, required=True)
    parser.add_argument("--copy-chunk-rows", type=int, required=True)
    args = parser.parse_args()

    if not args.zip_path.is_file():
        parser.error(f"archive not found: {args.zip_path}")
    if args.copy_chunk_rows <= 0:
        parser.error("--copy-chunk-rows must be positive")

    _configure_logging()
    conn = _db_connect()
    raw_file_id: int | None = None
    run_id: int | None = None
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO raw_files (filename, url, sha256, size_bytes, fecha_datos)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    args.zip_path.name,
                    args.zip_path.resolve().as_uri(),
                    hashlib.sha256(f"staging-benchmark-{uuid4()}".encode()).hexdigest(),
                    args.zip_path.stat().st_size,
                    args.fecha,
                ),
            )
            raw_file_id = cur.fetchone()[0]
            cur.execute(
                """
                INSERT INTO ingestion_runs (raw_file_id, status, fecha_solicitada, fecha_logica)
                VALUES (%s, 'running', %s, %s)
                RETURNING id
                """,
                (raw_file_id, args.fecha, args.fecha),
            )
            run_id = cur.fetchone()[0]
        conn.commit()

        started_at = perf_counter()
        result = loader.load(
            conn,
            args.zip_path,
            run_id,
            copy_chunk_rows=args.copy_chunk_rows,
        )
        elapsed_seconds = perf_counter() - started_at
        print(
            json.dumps(
                {
                    "copy_chunk_rows": args.copy_chunk_rows,
                    "elapsed_seconds": round(elapsed_seconds, 3),
                    "peak_rss_bytes": process_max_rss_bytes(),
                    **result.__dict__,
                },
                sort_keys=True,
            )
        )
    finally:
        if run_id is not None:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM stg_comercios WHERE run_id = %s", (run_id,))
                cur.execute("DELETE FROM stg_sucursales WHERE run_id = %s", (run_id,))
                cur.execute(f"DROP TABLE IF EXISTS stg_productos_{run_id}")
                cur.execute("DELETE FROM ingestion_runs WHERE id = %s", (run_id,))
        if raw_file_id is not None:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM raw_files WHERE id = %s", (raw_file_id,))
        conn.commit()
        conn.close()


if __name__ == "__main__":
    main()
