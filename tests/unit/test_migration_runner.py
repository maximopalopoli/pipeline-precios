from pathlib import Path


RUNNER = (Path(__file__).parents[2] / "db" / "run-migrations.sh").read_text()


def test_migration_runner_records_a_checksum_for_new_migrations():
    assert "checksum TEXT" in RUNNER
    assert "INSERT INTO schema_migrations (version, checksum)" in RUNNER
    assert "VALUES (:'migration_name', :'migration_checksum')" in RUNNER


def test_migration_runner_rejects_a_changed_applied_migration():
    assert 'migration_checksum="$(sha256sum "$migration_path"' in RUNNER
    assert "AND checksum <> :'migration_checksum'" in RUNNER
    assert "THEN 0 ELSE 1 END" in RUNNER


def test_migration_runner_baselines_existing_installations_once():
    assert "ALTER TABLE schema_migrations ADD COLUMN IF NOT EXISTS checksum TEXT" in RUNNER
    assert "AND checksum IS NULL" in RUNNER
