"""
warehouse_loader.py — Bulk-load dimensions and fact_precios into the star schema.

Strategy
--------
- Dimensions: COPY rows → temp table → INSERT ... ON CONFLICT (natural key) DO UPDATE
- fact_precios: COPY DataFrame → temp table → INSERT ... ON CONFLICT (grain) DO UPDATE
- Auto-creates monthly partitions for fact_precios when they are missing
- Registers ``rows_loaded`` in ``ingestion_runs`` after fact load

Load order:
  1. dim_comercio   (no FK deps)
  2. dim_producto   (no FK deps)
  3. dim_sucursal   (JOIN dim_comercio + dim_provincia inside the upsert SQL)
  4. fact_precios   (requires all surrogate keys already in dimensions)

Public API
----------
load(conn, comercios, sucursales, productos, fact_df, run_id) → LoadResult
load_dimensions(conn, comercios, sucursales, productos)       → (int, int, int)
load_fact(conn, df, run_id)                                   → int
register_rows_loaded(conn, run_id, rows_loaded)               → None
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import pandas as pd
import structlog

from etl.transform.cleaner import ComercioRow, ProductoRow, SucursalRow

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------


@dataclass
class LoadResult:
    rows_comercios: int
    rows_sucursales: int
    rows_productos: int
    rows_fact: int


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _copy_df(cur, df: pd.DataFrame, table: str) -> None:
    """Bulk-COPY a DataFrame into *table* using CSV format (empty string = NULL).

    Compatible with both psycopg2 (``copy_expert``) and psycopg3 (``copy``).
    """
    buf = io.StringIO()
    df.to_csv(buf, index=False, header=False, na_rep="")
    buf.seek(0)
    sql = f"COPY {table} FROM STDIN WITH (FORMAT CSV, NULL '')"
    if hasattr(cur, "copy_expert"):          # psycopg2
        cur.copy_expert(sql, buf)
    else:                                     # psycopg3
        with cur.copy(sql) as copy:
            copy.write(buf.read())


def _ensure_partition(conn, fecha_id: int) -> str:
    """
    Ensure the monthly RANGE partition for *fecha_id* exists in fact_precios.

    Partition name: ``fact_precios_YYYYMM``
    Bounds:         ``[YYYYMM01, YYYY(M+1)01)``   (integers, inclusive/exclusive)

    Returns the partition table name.
    """
    year = fecha_id // 10000
    month = (fecha_id // 100) % 100
    partition = f"fact_precios_{year:04d}{month:02d}"

    lo = year * 10000 + month * 100 + 1
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
    hi = next_year * 10000 + next_month * 100 + 1

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1
            FROM   pg_class c
            JOIN   pg_namespace n ON n.oid = c.relnamespace
            WHERE  c.relname = %s AND n.nspname = 'public'
            """,
            (partition,),
        )
        if cur.fetchone() is None:
            cur.execute(
                f"""
                CREATE TABLE {partition}
                    PARTITION OF fact_precios
                    FOR VALUES FROM ({lo}) TO ({hi})
                """
            )
            log.info(
                "warehouse.partition_created",
                partition=partition,
                from_id=lo,
                to_id=hi,
            )

    return partition


# ---------------------------------------------------------------------------
# Dimension loaders
# ---------------------------------------------------------------------------


def load_dimensions(
    conn,
    comercios: list[ComercioRow],
    sucursales: list[SucursalRow],
    productos: list[ProductoRow],
) -> tuple[int, int, int]:
    """
    Bulk-upsert all three dimension tables.

    Returns ``(rows_comercios, rows_sucursales, rows_productos)``.
    """
    rc = _load_dim_comercio(conn, comercios)
    rp = _load_dim_producto(conn, productos)
    rs = _load_dim_sucursal(conn, sucursales)

    log.info(
        "warehouse.dimensions_loaded",
        rows_comercios=rc,
        rows_sucursales=rs,
        rows_productos=rp,
    )
    return rc, rs, rp


def _load_dim_comercio(conn, rows: list[ComercioRow]) -> int:
    if not rows:
        return 0

    # Deduplicate by natural key; last occurrence wins (latest run)
    seen: dict[tuple[int, int], ComercioRow] = {}
    for r in rows:
        seen[(r.id_comercio, r.id_bandera)] = r

    df = pd.DataFrame(
        [
            {
                "id_comercio": r.id_comercio,
                "id_bandera": r.id_bandera,
                "comercio_cuit": r.comercio_cuit,
                "comercio_razon_social": r.comercio_razon_social,
                "comercio_bandera_nombre": r.comercio_bandera_nombre,
                "comercio_bandera_url": r.comercio_bandera_url,
            }
            for r in seen.values()
        ]
    )

    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TEMP TABLE tmp_dim_comercio (
                id_comercio             INT,
                id_bandera              INT,
                comercio_cuit           TEXT,
                comercio_razon_social   TEXT,
                comercio_bandera_nombre TEXT,
                comercio_bandera_url    TEXT
            ) ON COMMIT DROP
            """
        )
        _copy_df(cur, df, "tmp_dim_comercio")
        cur.execute(
            """
            INSERT INTO dim_comercio (
                id_comercio, id_bandera,
                comercio_cuit, comercio_razon_social,
                comercio_bandera_nombre, comercio_bandera_url
            )
            SELECT
                id_comercio, id_bandera,
                comercio_cuit, comercio_razon_social,
                comercio_bandera_nombre, comercio_bandera_url
            FROM tmp_dim_comercio
            ON CONFLICT (id_comercio, id_bandera) DO UPDATE SET
                comercio_cuit           = EXCLUDED.comercio_cuit,
                comercio_razon_social   = EXCLUDED.comercio_razon_social,
                comercio_bandera_nombre = EXCLUDED.comercio_bandera_nombre,
                comercio_bandera_url    = EXCLUDED.comercio_bandera_url,
                updated_at              = NOW()
            """
        )
        return cur.rowcount


def _load_dim_producto(conn, rows: list[ProductoRow]) -> int:
    if not rows:
        return 0

    seen: dict[str, ProductoRow] = {}
    for r in rows:
        seen[r.id_producto] = r

    df = pd.DataFrame(
        [
            {
                "id_producto": r.id_producto,
                "es_ean": r.es_ean,
                "productos_descripcion": r.productos_descripcion,
                "productos_marca": r.productos_marca,
                "productos_cantidad_presentacion": r.productos_cantidad_presentacion,
                "productos_unidad_medida_presentacion": r.productos_unidad_medida_presentacion,
                "productos_cantidad_referencia": r.productos_cantidad_referencia,
                "productos_unidad_medida_referencia": r.productos_unidad_medida_referencia,
            }
            for r in seen.values()
        ]
    )

    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TEMP TABLE tmp_dim_producto (
                id_producto                          TEXT,
                es_ean                               BOOLEAN,
                productos_descripcion                TEXT,
                productos_marca                      TEXT,
                productos_cantidad_presentacion      TEXT,
                productos_unidad_medida_presentacion TEXT,
                productos_cantidad_referencia        TEXT,
                productos_unidad_medida_referencia   TEXT
            ) ON COMMIT DROP
            """
        )
        _copy_df(cur, df, "tmp_dim_producto")
        cur.execute(
            """
            INSERT INTO dim_producto (
                id_producto, es_ean,
                productos_descripcion, productos_marca,
                productos_cantidad_presentacion, productos_unidad_medida_presentacion,
                productos_cantidad_referencia,   productos_unidad_medida_referencia
            )
            SELECT
                id_producto, es_ean,
                productos_descripcion, productos_marca,
                productos_cantidad_presentacion, productos_unidad_medida_presentacion,
                productos_cantidad_referencia,   productos_unidad_medida_referencia
            FROM tmp_dim_producto
            ON CONFLICT (id_producto) DO UPDATE SET
                es_ean                               = EXCLUDED.es_ean,
                productos_descripcion                = EXCLUDED.productos_descripcion,
                productos_marca                      = EXCLUDED.productos_marca,
                productos_cantidad_presentacion      = EXCLUDED.productos_cantidad_presentacion,
                productos_unidad_medida_presentacion = EXCLUDED.productos_unidad_medida_presentacion,
                productos_cantidad_referencia        = EXCLUDED.productos_cantidad_referencia,
                productos_unidad_medida_referencia   = EXCLUDED.productos_unidad_medida_referencia,
                updated_at                           = NOW()
            """
        )
        return cur.rowcount


def _load_dim_sucursal(conn, rows: list[SucursalRow]) -> int:
    """
    Bulk-upsert dim_sucursal.

    The temp table carries ``sucursales_provincia`` (ISO code) so the INSERT
    can LEFT JOIN dim_provincia to resolve ``provincia_sk`` inline, mirroring
    the logic in dimension_resolver.resolve_sucursal.
    """
    if not rows:
        return 0

    seen: dict[tuple[int, int, int], SucursalRow] = {}
    for r in rows:
        seen[(r.id_comercio, r.id_bandera, r.id_sucursal)] = r

    df = pd.DataFrame(
        [
            {
                "id_comercio": r.id_comercio,
                "id_bandera": r.id_bandera,
                "id_sucursal": r.id_sucursal,
                "sucursales_nombre": r.sucursales_nombre,
                "sucursales_tipo": r.sucursales_tipo,
                "sucursales_calle": r.sucursales_calle,
                "sucursales_numero": r.sucursales_numero,
                "sucursales_barrio": r.sucursales_barrio,
                "sucursales_codigo_postal": r.sucursales_codigo_postal,
                "sucursales_localidad": r.sucursales_localidad,
                # Stored as TEXT so COPY works regardless of None; cast in SQL
                "sucursales_latitud": str(r.sucursales_latitud) if r.sucursales_latitud is not None else None,
                "sucursales_longitud": str(r.sucursales_longitud) if r.sucursales_longitud is not None else None,
                # Kept only for the provincia_sk JOIN; not persisted in dim_sucursal
                "sucursales_provincia": r.sucursales_provincia,
            }
            for r in seen.values()
        ]
    )

    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TEMP TABLE tmp_dim_sucursal (
                id_comercio              INT,
                id_bandera               INT,
                id_sucursal              INT,
                sucursales_nombre        TEXT,
                sucursales_tipo          TEXT,
                sucursales_calle         TEXT,
                sucursales_numero        TEXT,
                sucursales_barrio        TEXT,
                sucursales_codigo_postal TEXT,
                sucursales_localidad     TEXT,
                sucursales_latitud       TEXT,
                sucursales_longitud      TEXT,
                sucursales_provincia     TEXT
            ) ON COMMIT DROP
            """
        )
        _copy_df(cur, df, "tmp_dim_sucursal")
        cur.execute(
            """
            INSERT INTO dim_sucursal (
                id_comercio, id_bandera, id_sucursal,
                comercio_sk,
                provincia_sk,
                sucursales_nombre, sucursales_tipo,
                sucursales_calle,  sucursales_numero,
                sucursales_barrio, sucursales_codigo_postal, sucursales_localidad,
                sucursales_latitud, sucursales_longitud
            )
            SELECT
                t.id_comercio, t.id_bandera, t.id_sucursal,
                dc.comercio_sk,
                dp.provincia_sk,
                t.sucursales_nombre, t.sucursales_tipo,
                t.sucursales_calle,  t.sucursales_numero,
                t.sucursales_barrio, t.sucursales_codigo_postal, t.sucursales_localidad,
                NULLIF(t.sucursales_latitud,  '')::DECIMAL(9, 6),
                NULLIF(t.sucursales_longitud, '')::DECIMAL(9, 6)
            FROM tmp_dim_sucursal t
            JOIN  dim_comercio  dc ON dc.id_comercio = t.id_comercio
                                  AND dc.id_bandera  = t.id_bandera
            LEFT JOIN dim_provincia dp ON dp.codigo_iso = t.sucursales_provincia
            ON CONFLICT (id_comercio, id_bandera, id_sucursal) DO UPDATE SET
                comercio_sk              = EXCLUDED.comercio_sk,
                provincia_sk             = EXCLUDED.provincia_sk,
                sucursales_nombre        = EXCLUDED.sucursales_nombre,
                sucursales_tipo          = EXCLUDED.sucursales_tipo,
                sucursales_calle         = EXCLUDED.sucursales_calle,
                sucursales_numero        = EXCLUDED.sucursales_numero,
                sucursales_barrio        = EXCLUDED.sucursales_barrio,
                sucursales_codigo_postal = EXCLUDED.sucursales_codigo_postal,
                sucursales_localidad     = EXCLUDED.sucursales_localidad,
                sucursales_latitud       = EXCLUDED.sucursales_latitud,
                sucursales_longitud      = EXCLUDED.sucursales_longitud,
                updated_at               = NOW()
            """
        )
        return cur.rowcount


# ---------------------------------------------------------------------------
# Fact loader
# ---------------------------------------------------------------------------


def load_fact(conn, df: pd.DataFrame, run_id: int) -> int:
    """
    Bulk-load *df* (output of ``fact_builder.build_fact_df``) into fact_precios.

    1. Ensure monthly partitions exist for every unique fecha_id in *df*.
    2. COPY *df* → temp table.
    3. INSERT from temp → fact_precios with ON CONFLICT (producto_sk, sucursal_sk, fecha_id)
       DO UPDATE (SCD Type 1 on prices).

    Returns the number of rows upserted.
    """
    if df.empty:
        log.info("warehouse.fact_skipped", reason="empty_dataframe", run_id=run_id)
        return 0

    for fecha_id in df["fecha_id"].unique():
        _ensure_partition(conn, int(fecha_id))

    cols = [
        "producto_sk",
        "sucursal_sk",
        "fecha_id",
        "run_id",
        "precio_lista",
        "precio_referencia",
        "precio_promo1",
        "precio_promo2",
        "leyenda_promo1",
        "leyenda_promo2",
        "tiene_promo",
    ]

    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TEMP TABLE tmp_fact_precios (
                producto_sk       INT,
                sucursal_sk       INT,
                fecha_id          INT,
                run_id            INT,
                precio_lista      NUMERIC(12, 2),
                precio_referencia NUMERIC(12, 2),
                precio_promo1     NUMERIC(12, 2),
                precio_promo2     NUMERIC(12, 2),
                leyenda_promo1    TEXT,
                leyenda_promo2    TEXT,
                tiene_promo       BOOLEAN
            ) ON COMMIT DROP
            """
        )
        _copy_df(cur, df[cols], "tmp_fact_precios")
        cur.execute(
            """
            INSERT INTO fact_precios (
                producto_sk, sucursal_sk, fecha_id, run_id,
                precio_lista, precio_referencia,
                precio_promo1, precio_promo2,
                leyenda_promo1, leyenda_promo2,
                tiene_promo
            )
            SELECT DISTINCT ON (producto_sk, sucursal_sk, fecha_id)
                producto_sk, sucursal_sk, fecha_id, run_id,
                precio_lista, precio_referencia,
                precio_promo1, precio_promo2,
                leyenda_promo1, leyenda_promo2,
                tiene_promo
            FROM tmp_fact_precios
            ORDER BY producto_sk, sucursal_sk, fecha_id
            ON CONFLICT (producto_sk, sucursal_sk, fecha_id) DO UPDATE SET
                run_id            = EXCLUDED.run_id,
                precio_lista      = EXCLUDED.precio_lista,
                precio_referencia = EXCLUDED.precio_referencia,
                precio_promo1     = EXCLUDED.precio_promo1,
                precio_promo2     = EXCLUDED.precio_promo2,
                leyenda_promo1    = EXCLUDED.leyenda_promo1,
                leyenda_promo2    = EXCLUDED.leyenda_promo2,
                tiene_promo       = EXCLUDED.tiene_promo
            """
        )
        count = cur.rowcount

    log.info("warehouse.fact_loaded", run_id=run_id, rows=count)
    return count


# ---------------------------------------------------------------------------
# Ingestion run tracking
# ---------------------------------------------------------------------------


def register_rows_loaded(conn, run_id: int, rows_loaded: int) -> None:
    """Persist ``rows_loaded`` into ``ingestion_runs`` for the given run."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE ingestion_runs SET rows_loaded = %s WHERE id = %s",
            (rows_loaded, run_id),
        )
    log.info(
        "warehouse.rows_loaded_registered",
        run_id=run_id,
        rows_loaded=rows_loaded,
    )


# ---------------------------------------------------------------------------
# Chunked / streaming helpers
# ---------------------------------------------------------------------------


def load_dim_produto_from_staging(conn, run_id: int) -> int:
    """
    Load dim_produto directly from stg_produtos via SQL (no Python intermediary).

    Avoids loading 14M+ rows into Python memory: the DB does the deduplication
    and type-casting inline.  Returns the number of rows upserted.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO dim_producto (
                id_producto, es_ean,
                productos_descripcion, productos_marca,
                productos_cantidad_presentacion, productos_unidad_medida_presentacion,
                productos_cantidad_referencia,   productos_unidad_medida_referencia
            )
            SELECT DISTINCT ON (TRIM(id_producto))
                TRIM(id_producto),
                (TRIM(productos_ean) = '1'),
                TRIM(productos_descripcion),
                TRIM(productos_marca),
                TRIM(productos_cantidad_presentacion),
                TRIM(productos_unidad_medida_presentacion),
                TRIM(productos_cantidad_referencia),
                TRIM(productos_unidad_medida_referencia)
            FROM stg_productos
            WHERE run_id = %s
              AND TRIM(productos_ean) IN ('0', '1')
              AND TRIM(id_producto)          <> ''
              AND TRIM(productos_descripcion) <> ''
            ORDER BY TRIM(id_producto)
            ON CONFLICT (id_producto) DO UPDATE SET
                es_ean                               = EXCLUDED.es_ean,
                productos_descripcion                = EXCLUDED.productos_descripcion,
                productos_marca                      = EXCLUDED.productos_marca,
                productos_cantidad_presentacion      = EXCLUDED.productos_cantidad_presentacion,
                productos_unidad_medida_presentacion = EXCLUDED.productos_unidad_medida_presentacion,
                productos_cantidad_referencia        = EXCLUDED.productos_cantidad_referencia,
                productos_unidad_medida_referencia   = EXCLUDED.productos_unidad_medida_referencia,
                updated_at                           = NOW()
            """,
            (run_id,),
        )
        count = cur.rowcount
    log.info("warehouse.dim_producto_loaded_from_staging", run_id=run_id, rows=count)
    return count


def load_dimensions_no_producto(
    conn,
    comercios: list[ComercioRow],
    sucursales: list[SucursalRow],
) -> tuple[int, int]:
    """Upsert dim_comercio and dim_sucursal only (dim_produto loaded separately via SQL)."""
    rc = _load_dim_comercio(conn, comercios)
    rs = _load_dim_sucursal(conn, sucursales)
    log.info("warehouse.dimensions_loaded", rows_comercios=rc, rows_sucursales=rs)
    return rc, rs


# ---------------------------------------------------------------------------
# High-level entry point
# ---------------------------------------------------------------------------


def load(
    conn,
    comercios: list[ComercioRow],
    sucursales: list[SucursalRow],
    productos: list[ProductoRow],
    fact_df: pd.DataFrame,
    run_id: int,
) -> LoadResult:
    """
    Full warehouse load for one ingestion run.

    Execution order:
      1. dim_comercio + dim_producto (no FK deps)
      2. dim_sucursal (depends on dim_comercio being committed/visible)
      3. fact_precios (depends on all three dimensions)
      4. Register rows_loaded in ingestion_runs

    Transaction management (commit / rollback) is the caller's responsibility.
    """
    logger = log.bind(run_id=run_id)
    logger.info(
        "warehouse.load.start",
        n_comercios=len(comercios),
        n_sucursales=len(sucursales),
        n_productos=len(productos),
        n_fact=len(fact_df),
    )

    rc, rs, rp = load_dimensions(conn, comercios, sucursales, productos)
    rf = load_fact(conn, fact_df, run_id)
    register_rows_loaded(conn, run_id, rf)

    result = LoadResult(
        rows_comercios=rc,
        rows_sucursales=rs,
        rows_productos=rp,
        rows_fact=rf,
    )
    logger.info(
        "warehouse.load.done",
        rows_comercios=rc,
        rows_sucursales=rs,
        rows_productos=rp,
        rows_fact=rf,
    )
    return result
