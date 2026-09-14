#!/bin/sh

set -eu

db_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"

export PGPASSWORD="${POSTGRES_PASSWORD:?POSTGRES_PASSWORD must be set}"

script_path="$(mktemp)"
trap 'rm -f "$script_path"' EXIT

cat > "$script_path" <<'SQL'
\set ON_ERROR_STOP on

SELECT pg_advisory_lock(hashtext('pipeline-precios:migrations'));

CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
SQL

for migration_path in "$db_dir"/migrations/*.sql; do
    migration_name="$(basename "$migration_path")"

    cat >> "$script_path" <<SQL
\set migration_name '$migration_name'
SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM schema_migrations WHERE version = :'migration_name'
) THEN 'true' ELSE 'false' END AS should_apply \gset
\if :should_apply
BEGIN;
\i $migration_path
INSERT INTO schema_migrations (version) VALUES (:'migration_name');
COMMIT;
\endif
SQL
done

for seed_path in "$db_dir"/seeds/*.sql; do
    printf '\\i %s\n' "$seed_path" >> "$script_path"
done

cat >> "$script_path" <<'SQL'
SELECT pg_advisory_unlock(hashtext('pipeline-precios:migrations'));
SQL

psql \
    --host="${POSTGRES_HOST:-postgres}" \
    --username="$POSTGRES_USER" \
    --dbname="$POSTGRES_DB" \
    --file="$script_path"
