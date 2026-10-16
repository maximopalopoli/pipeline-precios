#!/bin/sh
# Scheduled ingestion of today's Argentine date, retried after a failure.
#
# The date is fixed once so every attempt ingests the same day. A completed
# file is skipped by the pipeline itself, so a retry never publishes twice;
# a failed attempt keeps the previous snapshot visible and the pipeline sends
# its own alert.
set -u

cd "$(dirname "$0")/.."

# A local .env may bound a development run; a scheduled run always publishes.
unset SEPA_DEV_CHAIN_KEYS SEPA_DEV_MAX_PRODUCT_ROWS

python="${INGEST_PYTHON:-.venv/bin/python}"
attempts="${INGEST_ATTEMPTS:-3}"
retry_delay="${INGEST_RETRY_DELAY_SECONDS:-900}"
fecha="$(TZ=America/Argentina/Buenos_Aires date +%F)"

attempt=1
while :; do
    echo "ingest: $fecha attempt $attempt of $attempts"
    if "$python" -m etl.run_pipeline --fecha "$fecha"; then
        exit 0
    fi
    if [ "$attempt" -ge "$attempts" ]; then
        echo "ingest: $fecha failed after $attempts attempts" >&2
        exit 1
    fi
    attempt=$((attempt + 1))
    sleep "$retry_delay"
done
