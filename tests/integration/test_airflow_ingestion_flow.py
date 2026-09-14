"""Integration coverage for Airflow task callables without an Airflow runtime."""

import importlib.util
import sys
from datetime import date
from pathlib import Path
from types import ModuleType

import psycopg
import pytest

import etl.run_pipeline as cli_pipeline
from etl.extract.downloader import DownloadResult
from etl.staging import loader as staging_loader

_DAG_PATH = Path(__file__).parent.parent.parent / "dags" / "dag_sepa_ingest.py"
_SEEDS = Path(__file__).parent.parent.parent / "db" / "seeds"
_RUN_DATE = date(2026, 3, 15)


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

    def xcom_pull(self, *, task_ids: str):
        return self.values[task_ids]


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


def _create_raw_file(conn, filename: str, sha256: str) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO raw_files (filename, url, sha256, size_bytes, fecha_datos)
            VALUES (%s, 'http://example.test/data.zip', %s, 1, %s)
            RETURNING id
            """,
            (filename, sha256, _RUN_DATE),
        )
        return cur.fetchone()[0]


def _connect_to_fixture(pg_conn):
    return psycopg.connect(
        host=pg_conn.info.host,
        port=pg_conn.info.port,
        user=pg_conn.info.user,
        dbname=pg_conn.info.dbname,
    )


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
        facts = cur.fetchall()
        cur.execute(
            """
            SELECT producto_sk, provincia_sk, cantidad_obs, precio_lista_avg,
                   precio_lista_min, precio_lista_max, cantidad_con_promo
            FROM agg_precios_diarios
            WHERE fecha_id = %s
            ORDER BY producto_sk, provincia_sk
            """,
            (int(_RUN_DATE.strftime("%Y%m%d")),),
        )
        aggregates = cur.fetchall()
    return facts, aggregates


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
    assert pending == {"chunks": 1, "rows_fact": 7, "rows_valid": 7}
    with pg_conn.cursor() as cur:
        cur.execute("SELECT status, rows_valid, rows_loaded FROM ingestion_runs WHERE id = %s", (run_id,))
        assert cur.fetchone() == ("completed", 7, 7)
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
    monkeypatch.setattr(cli_pipeline, "_db_connect", connect)
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
    with pg_conn.cursor() as cur:
        cur.execute("SELECT id FROM ingestion_runs WHERE raw_file_id = %s", (raw_file_id,))
        cli_run_id = cur.fetchone()[0]
        cur.execute("SELECT status, rows_valid, rows_loaded FROM ingestion_runs WHERE id = %s", (cli_run_id,))
        assert cur.fetchone() == ("completed", 7, 7)
    cli_snapshot = _published_snapshot(pg_conn, cli_run_id)

    dag_run_id = _create_run(pg_conn)
    staging_loader.load(pg_conn, sample_zip_path, dag_run_id)
    pg_conn.commit()
    monkeypatch.setattr(dag_module, "_db_connect", connect)
    ti = _TaskInstance(
        {
            "load_staging": {"run_id": dag_run_id},
            "download_zip": {"fecha_datos": str(_RUN_DATE)},
        }
    )
    dag_module.load_dimensions(ti=ti)
    ti.values["load_pending_fact_precios"] = dag_module.load_pending_fact_precios(ti=ti)
    dag_module.publish_daily_snapshot(ti=ti)

    with pg_conn.cursor() as cur:
        cur.execute("SELECT status, rows_valid, rows_loaded FROM ingestion_runs WHERE id = %s", (dag_run_id,))
        assert cur.fetchone() == ("completed", 7, 7)
    assert _published_snapshot(pg_conn, dag_run_id) == cli_snapshot
