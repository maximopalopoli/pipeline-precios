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
    checksum TEXT,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Existing installations predate checksums.  Their current files become the
-- baseline on the first run; all subsequent runs verify that baseline.
ALTER TABLE schema_migrations ADD COLUMN IF NOT EXISTS checksum TEXT;
SQL

for migration_path in "$db_dir"/migrations/*.sql; do
    migration_name="$(basename "$migration_path")"
    migration_checksum="$(sha256sum "$migration_path" | cut -d ' ' -f1)"

    cat >> "$script_path" <<SQL
\set migration_name '$migration_name'
\set migration_checksum '$migration_checksum'
SELECT 1 / CASE WHEN EXISTS (
    SELECT 1
    FROM schema_migrations
    WHERE version = :'migration_name'
      AND checksum IS NOT NULL
      AND checksum <> :'migration_checksum'
) THEN 0 ELSE 1 END;
UPDATE schema_migrations
SET checksum = :'migration_checksum'
WHERE version = :'migration_name'
  AND checksum IS NULL;
SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM schema_migrations WHERE version = :'migration_name'
) THEN 'true' ELSE 'false' END AS should_apply \gset
\if :should_apply
BEGIN;
\i $migration_path
INSERT INTO schema_migrations (version, checksum)
VALUES (:'migration_name', :'migration_checksum');
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
