#!/usr/bin/env bash
# Run one complete, local SEPA replay for query-plan benchmarking.
#
# This publishes a full snapshot to the local PostgreSQL database. It does not
# contact the SEPA URL: the already downloaded source archive is required.

set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(cd -- "$script_dir/.." && pwd)"
cd "$project_dir"

readonly run_date="2026-09-22"
readonly archive_path="data/2026-09-22_martes_eaefeae3d9f2885a9e95558d936955be4f36e7cd2e69e4d4e064e3d44463b20d.zip"
readonly chunk_size="10000"
readonly artifacts_dir="benchmark-artifacts"
readonly log_path="${artifacts_dir}/ingestion-full-${run_date}.log"

if [[ ! -f "$archive_path" ]]; then
  echo "Required local archive not found: $archive_path" >&2
  exit 1
fi

if [[ ! -f .env ]]; then
  echo "Required .env file not found in $project_dir" >&2
  exit 1
fi

mkdir -p "$artifacts_dir"

set -a
# shellcheck disable=SC1091
source .env
set +a

echo "Applying migrations and starting PostgreSQL..." >&2
make db

echo "Starting full local replay for ${run_date}; output is also saved to ${log_path}." >&2
env -u SEPA_DEV_CHAIN_KEYS -u SEPA_DEV_MAX_PRODUCT_ROWS \
  SEPA_FACT_CHUNK_SIZE="$chunk_size" \
  .venv/bin/python -m etl.run_pipeline \
    --fecha "$run_date" \
    --zip-path "$archive_path" \
  2>&1 | tee "$log_path"

echo "Full replay completed. Next commands:" >&2
echo "  make etl-baseline" >&2
echo "  make audit-queries FECHA_ID=20260922 2>&1 | tee ${artifacts_dir}/query-audit-20260922.log" >&2
