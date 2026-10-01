"""Integration coverage for Airflow task callables without an Airflow runtime."""

import importlib.util
import sys
from datetime import date
from pathlib import Path
from types import ModuleType

import psycopg
import pytest

import etl.ingestion_processing as ingestion_processing
import etl.run_pipeline as cli_pipeline
import etl.source_archive as source_archive
from etl.extract.downloader import DownloadResult
from etl.ingestion_lock import IngestionLockBusyError, acquire_ingestion_lock
from etl.load.publisher import PublicationResult
from etl.load.warehouse_loader import _ensure_partition
from etl.retention_policy import RetentionPolicy
from etl.staging import loader as staging_loader

_DAG_PATH = Path(__file__).parent.parent.parent / "dags" / "dag_sepa_ingest.py"
_SEEDS = Path(__file__).parent.parent.parent / "db" / "seeds"
_RUN_DATE = date(2026, 3, 17)


class _Task:
    def __init__(self, *args, **kwargs):
        pass

    def __rshift__(self, other):
        return other


class _DAG:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _Variable:
    @staticmethod
    def get(*args, **kwargs):
        return kwargs.get("default_var")


class _TaskInstance:
    def __init__(self, values: dict[str, dict]):
        self.values = values
        self.pushed: dict[str, object] = {}

    def xcom_pull(self, *, task_ids: str, key: str | None = None):
        if key is None:
            return self.values[task_ids]
        return self.values.get(task_ids, {}).get(key, self.pushed.get(key))

    def xcom_push(self, *, key: str, value: object):
        self.pushed[key] = value


@pytest.fixture()
def dag_module(monkeypatch):
    airflow = ModuleType("airflow")
    airflow.DAG = _DAG
    airflow_models = ModuleType("airflow.models")
    airflow_models.Variable = _Variable
    airflow_python = ModuleType("airflow.operators.python")
    airflow_python.PythonOperator = _Task
    airflow_python.ShortCircuitOperator = _Task
    monkeypatch.setitem(sys.modules, "airflow", airflow)
    monkeypatch.setitem(sys.modules, "airflow.models", airflow_models)
    monkeypatch.setitem(sys.modules, "airflow.operators", ModuleType("airflow.operators"))
    monkeypatch.setitem(sys.modules, "airflow.operators.python", airflow_python)

    spec = importlib.util.spec_from_file_location("test_airflow_dag", _DAG_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def _create_run(conn) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO raw_files (filename, url, sha256, size_bytes, fecha_datos)
            VALUES ('airflow.zip', 'http://example.test/data.zip', 'airflow-flow', 1, %s)
            RETURNING id
            """,
            (_RUN_DATE,),
        )
        raw_file_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO ingestion_runs (raw_file_id) VALUES (%s) RETURNING id",
            (raw_file_id,),
        )
        return cur.fetchone()[0]


def _create_raw_file(
    conn, filename: str, sha256: str, fecha_datos: date = _RUN_DATE
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO raw_files (filename, url, sha256, size_bytes, fecha_datos)
            VALUES (%s, 'http://example.test/data.zip', %s, 1, %s)
            RETURNING id
            """,
            (filename, sha256, fecha_datos),
        )
        return cur.fetchone()[0]


def _connect_to_fixture(pg_conn):
    return psycopg.connect(
        host=pg_conn.info.host,
        port=pg_conn.info.port,
        user=pg_conn.info.user,
        dbname=pg_conn.info.dbname,
    )


def _airflow_lock_info(conn, raw_file_id: int) -> dict[str, str | int]:
    lock = acquire_ingestion_lock(
        conn,
        fecha_datos=_RUN_DATE,
        raw_file_id=raw_file_id,
        owner="airflow:test",
    )
    return {
        "fecha_datos": str(lock.fecha_datos),
        "raw_file_id": lock.raw_file_id,
        "owner": lock.owner,
        "token": lock.token,
        "acquired_at": lock.acquired_at.isoformat(),
        "expires_at": lock.expires_at.isoformat(),
    }


def _raw_file_id_for_run(conn, run_id: int) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT raw_file_id FROM ingestion_runs WHERE id = %s", (run_id,))
        return cur.fetchone()[0]


def _published_snapshot(conn, run_id: int):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                p.producto_identity,
                s.id_comercio,
                s.id_bandera,
                s.id_sucursal,
                f.precio_lista,
                f.precio_referencia,
                f.precio_promo1,
                f.precio_promo2,
                f.leyenda_promo1,
                f.leyenda_promo2,
                f.tiene_promo
            FROM fact_precios f
            JOIN dim_producto p ON p.producto_sk = f.producto_sk
            JOIN dim_sucursal s ON s.sucursal_sk = f.sucursal_sk
            WHERE f.run_id = %s
            ORDER BY p.producto_identity, s.id_comercio, s.id_bandera, s.id_sucursal
            """,
            (run_id,),
        )
        return cur.fetchall()


def test_airflow_tasks_stream_and_publish_staged_products(
    pg_conn, sample_zip_path, dag_module, monkeypatch
):
    with pg_conn.cursor() as cur:
        cur.execute((_SEEDS / "seed_provincias.sql").read_text())
        cur.execute((_SEEDS / "seed_fechas.sql").read_text())
    run_id = _create_run(pg_conn)
    staging_loader.load(pg_conn, sample_zip_path, run_id)
    pg_conn.commit()

    def connect():
        return _connect_to_fixture(pg_conn)

    monkeypatch.setattr(dag_module, "_db_connect", connect)
    ti = _TaskInstance(
        {
            "load_staging": {"run_id": run_id},
            "download_zip": {"fecha_datos": str(_RUN_DATE)},
            "check_file_changed": {
                "ingestion_lock": _airflow_lock_info(
                    pg_conn, _raw_file_id_for_run(pg_conn, run_id)
                )
            },
        }
    )

    dimensions = dag_module.load_dimensions(ti=ti)
    pending = dag_module.load_pending_fact_precios(ti=ti)
    ti.values["load_pending_fact_precios"] = pending
    dag_module.publish_daily_snapshot(ti=ti)

    assert dimensions == {
        "rows_clean_comercios": 2,
        "rows_clean_sucursales": 3,
        "rows_rejected": 0,
    }
    assert pending == {
        "chunks": 1,
        "rows_fact": 7,
        "rows_valid": 7,
        "rows_rejected": 0,
        "rows_without_keys": 0,
        "rows_deduplicated": 0,
    }
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT status, rows_valid, rows_loaded, rows_productos_rejected,
                   rows_productos_without_keys, rows_productos_deduplicated,
                   rows_fact_pending, rows_fact_published
            FROM ingestion_runs WHERE id = %s
            """,
            (run_id,),
        )
        assert cur.fetchone() == ("completed", 7, 7, 0, 0, 0, 7, 7)
        cur.execute("SELECT COUNT(*) FROM fact_precios WHERE run_id = %s", (run_id,))
        assert cur.fetchone()[0] == 7
        cur.execute("SELECT COUNT(*) FROM pending_fact_precios WHERE run_id = %s", (run_id,))
        assert cur.fetchone()[0] == 0


def test_cli_and_airflow_publish_equivalent_snapshots(
    pg_conn, sample_zip_path, tmp_path, dag_module, monkeypatch
):
    raw_file_id = _create_raw_file(pg_conn, "cli.zip", "cli-flow")
    pg_conn.commit()

    def connect():
        return _connect_to_fixture(pg_conn)

    original_run_all = cli_pipeline.quality_checks.run_all
    staging_retention_calls = []
    raw_retention_calls = []
    monkeypatch.setattr(cli_pipeline, "_db_connect", connect)
    monkeypatch.setattr(
        cli_pipeline,
        "apply_staging_retention",
        lambda conn, *, policy, fecha_datos: staging_retention_calls.append(
            (policy, fecha_datos)
        ),
    )
    archive_calls = []
    monkeypatch.delenv("SEPA_ARCHIVE_DIR", raising=False)
    monkeypatch.setattr(
        cli_pipeline,
        "archive_completed_raw_files",
        lambda conn, *, raw_dir, archive_dir: archive_calls.append((raw_dir, archive_dir)),
    )
    monkeypatch.setattr(
        cli_pipeline,
        "apply_raw_retention",
        lambda conn, *, raw_dir, archive_dir, policy, fecha_datos: (
            raw_retention_calls.append((raw_dir, archive_dir, policy, fecha_datos))
        ),
    )
    monkeypatch.setattr(
        cli_pipeline.downloader,
        "download",
        lambda conn, raw_dir, fecha: DownloadResult(
            raw_file_id=raw_file_id,
            filename="cli.zip",
            path=sample_zip_path,
            sha256="cli-flow",
            size_bytes=sample_zip_path.stat().st_size,
            fecha_datos=_RUN_DATE,
            is_new=True,
        ),
    )
    monkeypatch.setattr(
        cli_pipeline.quality_checks,
        "run_all",
        lambda conn, run_id: original_run_all(conn, run_id, min_productos=1),
    )

    cli_result = cli_pipeline.run(fecha=_RUN_DATE, raw_dir=tmp_path)
    assert cli_result.rows_loaded == 7
    assert staging_retention_calls == [(cli_pipeline.from_environment(), _RUN_DATE)]
    assert raw_retention_calls == [
        (tmp_path, tmp_path / "archive", cli_pipeline.from_environment(), _RUN_DATE)
    ]
    assert archive_calls == [(tmp_path, tmp_path / "archive")]
    with pg_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM ingestion_locks WHERE fecha_datos = %s", (_RUN_DATE,))
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT id FROM ingestion_runs WHERE raw_file_id = %s", (raw_file_id,))
        cli_run_id = cur.fetchone()[0]
        cur.execute(
            """
            SELECT status, rows_valid, rows_loaded, rows_productos_rejected,
                   rows_productos_without_keys, rows_productos_deduplicated,
                   rows_fact_pending, rows_fact_published
            FROM ingestion_runs WHERE id = %s
            """,
            (cli_run_id,),
        )
        assert cur.fetchone() == ("completed", 7, 7, 0, 0, 0, 7, 7)
    cli_snapshot = _published_snapshot(pg_conn, cli_run_id)

    dag_run_id = _create_run(pg_conn)
    staging_loader.load(pg_conn, sample_zip_path, dag_run_id)
    pg_conn.commit()
    monkeypatch.setattr(dag_module, "_db_connect", connect)
    ti = _TaskInstance(
        {
            "load_staging": {"run_id": dag_run_id},
            "download_zip": {"fecha_datos": str(_RUN_DATE)},
            "check_file_changed": {
                "ingestion_lock": _airflow_lock_info(
                    pg_conn, _raw_file_id_for_run(pg_conn, dag_run_id)
                )
            },
        }
    )
    dag_module.load_dimensions(ti=ti)
    ti.values["load_pending_fact_precios"] = dag_module.load_pending_fact_precios(ti=ti)
    dag_module.publish_daily_snapshot(ti=ti)

    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT status, rows_valid, rows_loaded, rows_productos_rejected,
                   rows_productos_without_keys, rows_productos_deduplicated,
                   rows_fact_pending, rows_fact_published
            FROM ingestion_runs WHERE id = %s
            """,
            (dag_run_id,),
        )
        assert cur.fetchone() == ("completed", 7, 7, 0, 0, 0, 7, 7)
    assert _published_snapshot(pg_conn, dag_run_id) == cli_snapshot


def test_cli_partial_run_is_isolated_and_a_later_full_run_publishes(
    pg_conn, sample_zip_path, tmp_path, monkeypatch
):
    raw_file_id = _create_raw_file(pg_conn, "bounded-cli.zip", "bounded-cli")
    pg_conn.commit()

    def connect():
        return _connect_to_fixture(pg_conn)

    original_run_all = cli_pipeline.quality_checks.run_all
    monkeypatch.setattr(cli_pipeline, "_db_connect", connect)
    monkeypatch.setattr(
        cli_pipeline.downloader,
        "download",
        lambda conn, raw_dir, fecha: DownloadResult(
            raw_file_id=raw_file_id,
            filename="bounded-cli.zip",
            path=sample_zip_path,
            sha256="bounded-cli",
            size_bytes=sample_zip_path.stat().st_size,
            fecha_datos=_RUN_DATE,
            is_new=True,
        ),
    )
    monkeypatch.setattr(
        cli_pipeline.quality_checks,
        "run_all",
        lambda conn, run_id: original_run_all(conn, run_id, min_productos=1),
    )
    monkeypatch.setenv("SEPA_DEV_CHAIN_KEYS", "1:1")
    monkeypatch.setenv("SEPA_DEV_MAX_PRODUCT_ROWS", "2")

    partial_result = cli_pipeline.run(fecha=_RUN_DATE, raw_dir=tmp_path)

    assert partial_result.rows_loaded == 2
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, is_partial, status, partial_chain_keys,
                   partial_max_product_rows, rows_fact_pending, rows_fact_published
            FROM ingestion_runs
            WHERE raw_file_id = %s
            """,
            (raw_file_id,),
        )
        partial_run = cur.fetchone()
        assert partial_run[1:] == (True, "completed", "1:1", 2, 2, 0)
        cur.execute("SELECT COUNT(*) FROM fact_precios WHERE fecha_id = %s", (20260317,))
        assert cur.fetchone()[0] == 0
        cur.execute(
            "SELECT COUNT(*) FROM pending_fact_precios WHERE run_id = %s",
            (partial_run[0],),
        )
        assert cur.fetchone()[0] == 2
    # The next full publication detaches the daily leaf. Release this fixture
    # connection's read locks before exercising that independent lifecycle.
    pg_conn.commit()

    monkeypatch.delenv("SEPA_DEV_CHAIN_KEYS")
    monkeypatch.delenv("SEPA_DEV_MAX_PRODUCT_ROWS")
    full_result = cli_pipeline.run(fecha=_RUN_DATE, raw_dir=tmp_path)

    assert full_result.rows_loaded == 7
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT is_partial, status, rows_fact_pending, rows_fact_published
            FROM ingestion_runs
            WHERE raw_file_id = %s
            ORDER BY id
            """,
            (raw_file_id,),
        )
        assert cur.fetchall() == [
            (True, "completed", 2, 0),
            (False, "completed", 7, 7),
        ]
        cur.execute("SELECT COUNT(*) FROM fact_precios WHERE fecha_id = %s", (20260317,))
        assert cur.fetchone()[0] == 7


def test_airflow_partial_run_completes_without_publishing_a_snapshot(
    pg_conn, sample_zip_path, dag_module, monkeypatch
):
    with pg_conn.cursor() as cur:
        cur.execute((_SEEDS / "seed_provincias.sql").read_text())
        cur.execute((_SEEDS / "seed_fechas.sql").read_text())
    raw_file_id = _create_raw_file(pg_conn, "bounded-airflow.zip", "bounded-airflow")
    pg_conn.commit()

    def connect():
        return _connect_to_fixture(pg_conn)

    monkeypatch.setattr(dag_module, "_db_connect", connect)
    monkeypatch.setenv("SEPA_DEV_CHAIN_KEYS", "1:1")
    monkeypatch.setenv("SEPA_DEV_MAX_PRODUCT_ROWS", "2")
    ti = _TaskInstance(
        {
            "download_zip": {
                "raw_file_id": raw_file_id,
                "is_new": True,
                "path": str(sample_zip_path),
                "fecha_datos": str(_RUN_DATE),
                "fecha_solicitada": str(_RUN_DATE),
                "fecha_logica": str(_RUN_DATE),
            }
        }
    )

    assert dag_module.check_file_changed(ti=ti, run_id="manual__partial") is True
    # Staging must use the policy selected with the lock, not a mutable worker
    # environment that no longer carries the development bounds.
    monkeypatch.delenv("SEPA_DEV_CHAIN_KEYS")
    monkeypatch.delenv("SEPA_DEV_MAX_PRODUCT_ROWS")
    staging = dag_module.load_staging(ti=ti)
    ti.values["load_staging"] = staging
    # Later tasks use the publication mode persisted on the ingestion run.
    dag_module.load_dimensions(ti=ti)
    ti.values["load_pending_fact_precios"] = dag_module.load_pending_fact_precios(ti=ti)
    dag_module.publish_daily_snapshot(ti=ti)

    assert staging["rows_productos"] == 2
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT is_partial, status, partial_chain_keys, partial_max_product_rows,
                   rows_fact_pending, rows_fact_published
            FROM ingestion_runs WHERE id = %s
            """,
            (staging["run_id"],),
        )
        assert cur.fetchone() == (True, "completed", "1:1", 2, 2, 0)
        cur.execute("SELECT COUNT(*) FROM fact_precios WHERE fecha_id = %s", (20260317,))
        assert cur.fetchone()[0] == 0
        cur.execute(
            "SELECT COUNT(*) FROM pending_fact_precios WHERE run_id = %s",
            (staging["run_id"],),
        )
        assert cur.fetchone()[0] == 2
        cur.execute("SELECT COUNT(*) FROM ingestion_locks WHERE fecha_datos = %s", (_RUN_DATE,))
        assert cur.fetchone()[0] == 0


def test_airflow_reserves_the_date_before_loading_staging(pg_conn, dag_module, monkeypatch):
    raw_file_id = _create_raw_file(pg_conn, "airflow-lock.zip", "airflow-lock")
    pg_conn.commit()
    monkeypatch.setattr(dag_module, "_db_connect", lambda: _connect_to_fixture(pg_conn))
    ti = _TaskInstance(
        {
            "download_zip": {
                "raw_file_id": raw_file_id,
                "is_new": True,
                "fecha_datos": str(_RUN_DATE),
            }
        }
    )

    assert dag_module.check_file_changed(ti=ti, run_id="manual__lock") is True
    assert ti.pushed["ingestion_lock"]["owner"] == "airflow:manual__lock"
    assert ti.pushed["development_policy"] == {
        "chain_keys": [],
        "max_product_rows": None,
    }
    assert ti.pushed["regional_scope"] == {"name": None}
    with pg_conn.cursor() as cur:
        cur.execute("SELECT owner FROM ingestion_locks WHERE fecha_datos = %s", (_RUN_DATE,))
        assert cur.fetchone()[0] == "airflow:manual__lock"

    dag_module._release_ingestion_lock_on_failure({"ti": ti})
    with pg_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM ingestion_locks WHERE fecha_datos = %s", (_RUN_DATE,))
        assert cur.fetchone()[0] == 0


@pytest.mark.parametrize(
    ("selected", "environment", "expected"),
    [("amba", "", "amba"), (None, "amba", None)],
    ids=["scope-removed-later", "scope-added-later"],
)
def test_staging_uses_the_scope_selected_with_the_lock(
    dag_module, monkeypatch, selected, environment, expected
):
    monkeypatch.setenv("SEPA_REGIONAL_SCOPE", environment)
    ti = _TaskInstance({"check_file_changed": {"regional_scope": {"name": selected}}})

    scope = dag_module._regional_scope_from_xcom(ti)

    assert (scope.name if scope else None) == expected


def test_cli_cannot_start_while_airflow_owns_the_same_date(
    pg_conn, dag_module, monkeypatch, tmp_path
):
    raw_file_id = _create_raw_file(pg_conn, "shared-lock.zip", "shared-lock")
    pg_conn.commit()

    def connect():
        return _connect_to_fixture(pg_conn)

    monkeypatch.setattr(dag_module, "_db_connect", connect)
    airflow_ti = _TaskInstance(
        {
            "download_zip": {
                "raw_file_id": raw_file_id,
                "is_new": True,
                "fecha_datos": str(_RUN_DATE),
            }
        }
    )
    assert dag_module.check_file_changed(ti=airflow_ti, run_id="manual__active") is True

    monkeypatch.setattr(cli_pipeline, "_db_connect", connect)
    monkeypatch.setattr(
        cli_pipeline.downloader,
        "download",
        lambda conn, raw_dir, fecha: DownloadResult(
            raw_file_id=raw_file_id,
            filename="shared-lock.zip",
            path=tmp_path / "not-read-before-lock.zip",
            sha256="shared-lock",
            size_bytes=1,
            fecha_datos=_RUN_DATE,
            is_new=True,
        ),
    )

    with pytest.raises(IngestionLockBusyError, match="already locked by airflow:manual__active"):
        cli_pipeline.run(fecha=_RUN_DATE, raw_dir=tmp_path)

    with pg_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM ingestion_runs WHERE raw_file_id = %s", (raw_file_id,))
        assert cur.fetchone()[0] == 0

    dag_module._release_ingestion_lock_on_failure({"ti": airflow_ti})
    retry_ti = _TaskInstance(airflow_ti.values)
    assert dag_module.check_file_changed(ti=retry_ti, run_id="manual__retry") is True
    dag_module._release_ingestion_lock_on_failure({"ti": retry_ti})


def test_airflow_applies_enabled_retention_after_publication(
    pg_conn, dag_module, monkeypatch, tmp_path
):
    raw_file_id = _create_raw_file(pg_conn, "airflow-retention.zip", "airflow-retention")
    with pg_conn.cursor() as cur:
        cur.execute("INSERT INTO ingestion_runs (raw_file_id) VALUES (%s) RETURNING id", (raw_file_id,))
        run_id = cur.fetchone()[0]
    _ensure_partition(pg_conn, 20250101)
    pg_conn.commit()

    def connect():
        return _connect_to_fixture(pg_conn)

    monkeypatch.setattr(dag_module, "_db_connect", connect)
    monkeypatch.setenv("SEPA_FACT_RETENTION_DAYS", "14")
    monkeypatch.setenv("SEPA_STAGING_RETENTION_DAYS", "7")
    monkeypatch.setenv("SEPA_RAW_RETENTION_DAYS", "30")
    monkeypatch.setattr(
        dag_module.Variable, "get", lambda *args, **kwargs: str(tmp_path)
    )
    staging_retention_calls = []
    raw_retention_calls = []
    monkeypatch.setattr(
        "etl.staging_retention.apply_staging_retention",
        lambda conn, *, policy, fecha_datos: staging_retention_calls.append(
            (policy, fecha_datos)
        ),
    )
    archive_calls = []
    monkeypatch.delenv("SEPA_ARCHIVE_DIR", raising=False)
    monkeypatch.setattr(
        "etl.raw_archive.archive_completed_raw_files",
        lambda conn, *, raw_dir, archive_dir: archive_calls.append((raw_dir, archive_dir)),
    )
    monkeypatch.setattr(
        "etl.raw_retention.apply_raw_retention",
        lambda conn, *, raw_dir, archive_dir, policy, fecha_datos: (
            raw_retention_calls.append((raw_dir, archive_dir, policy, fecha_datos))
        ),
    )
    monkeypatch.setattr(
        "etl.load.publisher.publish_pending_facts",
        lambda conn, **kwargs: PublicationResult(
            run_id=kwargs["run_id"],
            fecha_id=kwargs["fecha_id"],
            rows_fact=0,
        ),
    )
    ti = _TaskInstance(
        {
            "load_staging": {"run_id": run_id},
                "load_pending_fact_precios": {
                    "rows_valid": 0,
                    "rows_rejected": 0,
                    "rows_without_keys": 0,
                    "rows_deduplicated": 0,
                    "rows_fact": 0,
                },
            "download_zip": {"fecha_datos": str(_RUN_DATE)},
            "check_file_changed": {
                "ingestion_lock": _airflow_lock_info(pg_conn, raw_file_id)
            },
        }
    )

    dag_module.publish_daily_snapshot(ti=ti)

    assert staging_retention_calls == [
        (
            RetentionPolicy(
                fact_retention_days=14, staging_retention_days=7, raw_retention_days=30
            ),
            _RUN_DATE,
        )
    ]
    assert archive_calls == [(tmp_path, tmp_path / "archive")]
    assert raw_retention_calls == [
        (
            tmp_path,
            tmp_path / "archive",
            RetentionPolicy(
                fact_retention_days=14, staging_retention_days=7, raw_retention_days=30
            ),
            _RUN_DATE,
        )
    ]
    with pg_conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.fact_precios_202501')")
        assert cur.fetchone()[0] is None
        cur.execute("SELECT COUNT(*) FROM ingestion_locks WHERE fecha_datos = %s", (_RUN_DATE,))
        assert cur.fetchone()[0] == 0


def test_airflow_marks_the_run_failed_when_dimension_loading_fails(
    pg_conn, dag_module, monkeypatch
):
    run_id = _create_run(pg_conn)
    pg_conn.commit()

    def connect():
        return _connect_to_fixture(pg_conn)

    def fail_dimension_load(conn, run_id, **kwargs):
        raise RuntimeError("dimension load failed")

    monkeypatch.setattr(dag_module, "_db_connect", connect)
    monkeypatch.setattr(ingestion_processing, "load_staged_dimensions", fail_dimension_load)
    ti = _TaskInstance({"load_staging": {"run_id": run_id}})

    with pytest.raises(RuntimeError, match="dimension load failed"):
        dag_module.load_dimensions(ti=ti)

    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT status, finished_at IS NOT NULL, error_message
            FROM ingestion_runs
            WHERE id = %s
            """,
            (run_id,),
        )
        assert cur.fetchone() == ("failed", True, "dimension load failed")


def test_airflow_marks_a_new_run_failed_when_staging_fails(
    pg_conn, dag_module, monkeypatch
):
    raw_file_id = _create_raw_file(pg_conn, "staging-failure.zip", "staging-failure")
    pg_conn.commit()

    def connect():
        return _connect_to_fixture(pg_conn)

    def fail_staging_load(conn, zip_path, run_id, heartbeat=None, **kwargs):
        raise RuntimeError("staging load failed")

    monkeypatch.setattr(dag_module, "_db_connect", connect)
    monkeypatch.setattr(staging_loader, "load", fail_staging_load)
    monkeypatch.setattr(
        source_archive, "inspect_source_archive_date", lambda path: _RUN_DATE
    )
    ti = _TaskInstance({"download_zip": {"raw_file_id": raw_file_id, "path": "/tmp/data.zip"}})

    with pytest.raises(RuntimeError, match="staging load failed"):
        dag_module.load_staging(ti=ti)

    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT status, finished_at IS NOT NULL, error_message
            FROM ingestion_runs
            WHERE raw_file_id = %s
            """,
            (raw_file_id,),
        )
        assert cur.fetchone() == ("failed", True, "staging load failed")


def test_airflow_marks_source_date_mismatch_failed_and_keeps_audit_dates(
    pg_conn, sample_zip_path, dag_module, monkeypatch
):
    requested_date = date(2026, 3, 16)
    raw_file_id = _create_raw_file(
        pg_conn,
        "source-date-mismatch.zip",
        "source-date-mismatch-airflow",
        fecha_datos=requested_date,
    )
    pg_conn.commit()

    monkeypatch.setattr(
        dag_module, "_db_connect", lambda: _connect_to_fixture(pg_conn)
    )
    ti = _TaskInstance(
        {
            "download_zip": {
                "raw_file_id": raw_file_id,
                "path": str(sample_zip_path),
                "fecha_datos": str(requested_date),
                "fecha_solicitada": str(requested_date),
                "fecha_logica": str(requested_date),
            }
        }
    )

    with pytest.raises(ValueError, match="does not match logical ingestion date"):
        dag_module.load_staging(ti=ti)

    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT status, fecha_solicitada, fecha_logica, fecha_fuente
            FROM ingestion_runs
            WHERE raw_file_id = %s
            """,
            (raw_file_id,),
        )
        assert cur.fetchone() == (
            "failed",
            requested_date,
            requested_date,
            date(2026, 3, 17),
        )


def test_airflow_keeps_structured_quality_failure_details(
    pg_conn, sample_zip_path, dag_module, monkeypatch
):
    run_id = _create_run(pg_conn)
    staging_loader.load(pg_conn, sample_zip_path, run_id)
    pg_conn.commit()

    def connect():
        return _connect_to_fixture(pg_conn)

    monkeypatch.setattr(dag_module, "_db_connect", connect)
    ti = _TaskInstance({"load_staging": {"run_id": run_id}})

    with pytest.raises(cli_pipeline.quality_checks.QualityCheckError):
        dag_module.run_quality_checks(ti=ti)

    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT status, finished_at IS NOT NULL, error_message
            FROM ingestion_runs
            WHERE id = %s
            """,
            (run_id,),
        )
        status, finished, message = cur.fetchone()

    assert status == "failed"
    assert finished is True
    assert "quality_failures" in message
    assert "min_row_count" in message


def test_airflow_marks_the_run_failed_when_pending_fact_loading_fails(
    pg_conn, dag_module, monkeypatch
):
    run_id = _create_run(pg_conn)
    pg_conn.commit()

    def connect():
        return _connect_to_fixture(pg_conn)

    def fail_fact_load(conn, connect, *, run_id, run_date, chunk_size, heartbeat=None):
        raise RuntimeError("pending fact load failed")

    monkeypatch.setattr(dag_module, "_db_connect", connect)
    monkeypatch.setattr(ingestion_processing, "stream_pending_product_facts", fail_fact_load)
    ti = _TaskInstance(
        {
            "load_staging": {"run_id": run_id},
            "download_zip": {"fecha_datos": str(_RUN_DATE)},
        }
    )

    with pytest.raises(RuntimeError, match="pending fact load failed"):
        dag_module.load_pending_fact_precios(ti=ti)

    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT status, error_message FROM ingestion_runs WHERE id = %s",
            (run_id,),
        )
        assert cur.fetchone() == ("failed", "pending fact load failed")
