"""Curated overrides correct the voted product attributes when reading."""

import asyncio
import re
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest

from api.routes import products

IDENTITY = "ean:7791234567898"
SEED = Path(__file__).parent.parent.parent / "db" / "seeds" / "seed_producto_overrides.sql"


@pytest.fixture()
def product(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO raw_files (filename, url, sha256, size_bytes, fecha_datos)
            VALUES ('test.zip', 'http://test', 'overrides', 1, '2026-03-24')
            RETURNING id
            """
        )
        raw_file_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO ingestion_runs (raw_file_id, status) VALUES (%s, 'completed') RETURNING id",
            (raw_file_id,),
        )
        run_id = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO dim_producto (
                producto_identity, id_producto, es_ean,
                productos_descripcion, productos_marca,
                productos_cantidad_presentacion, productos_unidad_medida_presentacion,
                contenido, contenido_unidad, cadenas_fuente
            ) VALUES (%s, '7791234567898', TRUE, 'LECHE ENT', 'SERENISIMA',
                      '1', 'ML', 1, 'ml', 4)
            """,
            (IDENTITY,),
        )
        cur.execute(
            """
            INSERT INTO producto_conflictos (producto_identity, atributo, valores, run_id)
            VALUES (%s, 'marca', '[]', %s), (%s, 'contenido', '[]', %s)
            """,
            (IDENTITY, run_id, IDENTITY, run_id),
        )
    return IDENTITY


def _override(pg_conn, **columns):
    columns = {"producto_identity": IDENTITY, "nota": "Checked on the package", **columns}
    names = ", ".join(columns)
    placeholders = ", ".join(["%s"] * len(columns))
    with pg_conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO producto_overrides ({names}) VALUES ({placeholders})",
            tuple(columns.values()),
        )


def _read(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT productos_descripcion, productos_marca,
                   productos_cantidad_presentacion, productos_unidad_medida_presentacion,
                   contenido, contenido_unidad, corregido
            FROM v_producto WHERE producto_identity = %s
            """,
            (IDENTITY,),
        )
        return cur.fetchone()


def _pending(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT atributo FROM v_producto_conflictos_pendientes ORDER BY atributo"
        )
        return [row[0] for row in cur.fetchall()]


def test_without_an_override_the_view_shows_the_vote(pg_conn, product):
    assert _read(pg_conn) == (
        "LECHE ENT", "SERENISIMA", "1", "ML", Decimal("1"), "ml", False
    )
    assert _pending(pg_conn) == ["contenido", "marca"]


def test_an_override_replaces_only_its_columns(pg_conn, product):
    _override(pg_conn, marca="LA SERENISIMA", contenido=Decimal("1000.00"),
              contenido_unidad="ml")

    assert _read(pg_conn) == (
        "LECHE ENT", "LA SERENISIMA", "1000", "ml", Decimal("1000.00"), "ml", True
    )


def test_removing_an_override_restores_the_vote(pg_conn, product):
    _override(pg_conn, descripcion="Leche entera 1 l")
    assert _read(pg_conn)[0] == "Leche entera 1 l"

    with pg_conn.cursor() as cur:
        cur.execute("DELETE FROM producto_overrides")

    assert _read(pg_conn)[0] == "LECHE ENT"


def test_an_override_settles_only_its_own_conflict(pg_conn, product):
    _override(pg_conn, marca="LA SERENISIMA")

    assert _pending(pg_conn) == ["contenido"]


@pytest.mark.parametrize(
    "columns",
    [
        pytest.param({}, id="no attribute"),
        pytest.param({"contenido": Decimal("1000")}, id="size without unit"),
        pytest.param({"contenido": Decimal("1"), "contenido_unidad": "kg"}, id="unknown unit"),
        pytest.param({"contenido": Decimal("0"), "contenido_unidad": "g"}, id="zero size"),
        pytest.param({"marca": " "}, id="blank brand"),
        pytest.param({"marca": "X", "nota": " "}, id="blank note"),
    ],
)
def test_invalid_overrides_are_rejected(pg_conn, product, columns):
    with pytest.raises(psycopg.errors.CheckViolation):
        _override(pg_conn, **columns)


def test_product_search_returns_the_corrected_attributes(pg_conn, product, monkeypatch):
    _override(pg_conn, descripcion="Leche entera 1 l", marca="LA SERENISIMA")

    async def fetch(sql: str, *params):
        with pg_conn.cursor() as cur:
            # asyncpg reuses $1; name the placeholders so psycopg can too.
            cur.execute(
                re.sub(r"\$(\d+)", r"%(p\1)s", sql),
                {f"p{i}": value for i, value in enumerate(params, start=1)},
            )
            columns = [column.name for column in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]

    monkeypatch.setattr(products, "fetch", fetch)

    [found] = asyncio.run(products.search_products("entera"))

    assert (found["descripcion"], found["marca"]) == ("Leche entera 1 l", "LA SERENISIMA")


def test_the_seed_replaces_the_table(pg_conn, product):
    _override(pg_conn, marca="EDITED BY HAND")
    seed = SEED.read_text().replace("BEGIN;", "").replace("COMMIT;", "")

    with pg_conn.cursor() as cur:
        cur.execute(seed)
        cur.execute("SELECT COUNT(*) FROM producto_overrides")
        assert cur.fetchone()[0] == 0
