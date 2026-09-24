"""
warehouse_loader.py — Bulk-load dimensions and fact_precios into the star schema.

Strategy
--------
- Dimensions: COPY rows → temp table → INSERT ... ON CONFLICT (natural key) DO UPDATE
- fact_precios: COPY DataFrame → temp table → INSERT ... ON CONFLICT (grain) DO UPDATE
- pending_fact_precios: COPY DataFrame → temp table → rows isolated by ingestion run
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
load_pending_fact(conn, df, run_id)                           → int
register_rows_loaded(conn, run_id, rows_loaded)               → None
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd
import structlog

from etl.transform.cleaner import ComercioRow, ProductoRow, SucursalRow
from etl.transform.product_identity import product_identity

log = structlog.get_logger(__name__)

_PENDING_FACT_COLUMNS = [
    "run_id",
    "producto_sk",
    "sucursal_sk",
    "fecha_id",
    "precio_lista",
    "precio_referencia",
    "precio_promo1",
    "precio_promo2",
    "leyenda_promo1",
    "leyenda_promo2",
    "tiene_promo",
]

_PENDING_FACT_TEMP_COLUMNS = [
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
    Ensure monthly and daily RANGE partitions for *fecha_id* exist in fact_precios.

    Returns the daily leaf table name ``fact_precios_YYYYMMDD``.

    Returns the partition table name.
    """
    year = fecha_id // 10000
    month = (fecha_id // 100) % 100
    monthly_partition = f"fact_precios_{year:04d}{month:02d}"
    daily_partition = f"fact_precios_{fecha_id}"

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
            (monthly_partition,),
        )
        if cur.fetchone() is None:
            cur.execute(
                f"""
                CREATE TABLE {monthly_partition}
                    PARTITION OF fact_precios
                    FOR VALUES FROM ({lo}) TO ({hi})
                    PARTITION BY RANGE (fecha_id)
                """
            )
            log.info(
                "warehouse.partition_created",
                partition=monthly_partition,
                from_id=lo,
                to_id=hi,
            )

        day = date(year, month, fecha_id % 100)
        next_day = day + timedelta(days=1)
        cur.execute("SELECT to_regclass(%s)", (f"public.{daily_partition}",))
        if cur.fetchone()[0] is None:
            cur.execute(
                """
                SELECT child.relname
                FROM pg_inherits AS inheritance
                JOIN pg_class AS parent ON parent.oid = inheritance.inhparent
                JOIN pg_class AS child ON child.oid = inheritance.inhrelid
                JOIN pg_namespace AS namespace ON namespace.oid = child.relnamespace
                WHERE parent.relname = %s
                  AND namespace.nspname = 'public'
                  AND pg_get_expr(child.relpartbound, child.oid) = %s
                """,
                (
                    monthly_partition,
                    f"FOR VALUES FROM ({fecha_id}) TO ({int(next_day.strftime('%Y%m%d'))})",
                ),
            )
            existing_daily_partition = cur.fetchone()
            if existing_daily_partition is not None:
                return existing_daily_partition[0]
            cur.execute(
                f"""
                CREATE TABLE {daily_partition}
                    PARTITION OF {monthly_partition}
                    FOR VALUES FROM ({fecha_id}) TO ({int(next_day.strftime('%Y%m%d'))})
                """
            )
            log.info("warehouse.daily_partition_created", partition=daily_partition)

    return daily_partition


def delete_fact_for_fecha(conn, fecha_id: int) -> int:
    """Delete all fact_precios rows for *fecha_id* (pre-load wipe).

    The partition is ensured first so the DELETE never fails on a missing table.
    Caller must COMMIT after this call.
    """
    _ensure_partition(conn, fecha_id)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM fact_precios WHERE fecha_id = %s", (fecha_id,))
        count = cur.rowcount
    log.info("warehouse.fact_deleted_for_fecha", fecha_id=fecha_id, rows_deleted=count)
    return count


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


def _load_dim_comercio(
    conn, rows: list[ComercioRow], *, update_existing: bool = True
) -> int:
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
            WHERE %s
            """
            ,
            (update_existing,),
        )
        return cur.rowcount


def _load_dim_producto(
    conn, rows: list[ProductoRow], *, update_existing: bool = True
) -> int:
    if not rows:
        return 0

    seen: dict[str, ProductoRow] = {}
    for r in rows:
        seen[product_identity(r)] = r

    df = pd.DataFrame(
        [
            {
                "producto_identity": product_identity(r),
                "source_id_comercio": r.id_comercio if product_identity(r).startswith("chain:") else None,
                "source_id_bandera": r.id_bandera if product_identity(r).startswith("chain:") else None,
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
                producto_identity                    TEXT,
                source_id_comercio                   INT,
                source_id_bandera                    INT,
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
                producto_identity, source_id_comercio, source_id_bandera,
                id_producto, es_ean,
                productos_descripcion, productos_marca,
                productos_cantidad_presentacion, productos_unidad_medida_presentacion,
                productos_cantidad_referencia,   productos_unidad_medida_referencia
            )
            SELECT
                producto_identity, source_id_comercio, source_id_bandera,
                id_producto, es_ean,
                productos_descripcion, productos_marca,
                productos_cantidad_presentacion, productos_unidad_medida_presentacion,
                productos_cantidad_referencia,   productos_unidad_medida_referencia
            FROM tmp_dim_producto
            ON CONFLICT (producto_identity) DO UPDATE SET
                es_ean                               = EXCLUDED.es_ean,
                productos_descripcion                = EXCLUDED.productos_descripcion,
                productos_marca                      = EXCLUDED.productos_marca,
                productos_cantidad_presentacion      = EXCLUDED.productos_cantidad_presentacion,
                productos_unidad_medida_presentacion = EXCLUDED.productos_unidad_medida_presentacion,
                productos_cantidad_referencia        = EXCLUDED.productos_cantidad_referencia,
                productos_unidad_medida_referencia   = EXCLUDED.productos_unidad_medida_referencia,
                updated_at                           = NOW()
            WHERE %s
            """
            ,
            (update_existing,),
        )
        return cur.rowcount


def _load_dim_sucursal(
    conn, rows: list[SucursalRow], *, update_existing: bool = True
) -> int:
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
            WHERE %s
            """
            ,
            (update_existing,),
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
            SELECT producto_sk, sucursal_sk, fecha_id, run_id,
                precio_lista, precio_referencia,
                precio_promo1, precio_promo2,
                leyenda_promo1, leyenda_promo2,
                tiene_promo
            FROM tmp_fact_precios
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


def load_pending_fact(conn, df: pd.DataFrame, run_id: int) -> int:
    """Load one fact chunk for *run_id* without publishing it to the warehouse.

    The caller may commit each chunk.  Those commits only make data visible in
    ``pending_fact_precios``; consumers continue reading ``fact_precios`` until
    the later publication step moves a fully validated run atomically.
    """
    if df.empty:
        log.info("warehouse.pending_fact_skipped", reason="empty_dataframe", run_id=run_id)
        return 0

    pending_df = df.copy()
    pending_df["run_id"] = run_id

    # The usual path has no duplicate pending grain: facts are deduplicated in
    # the builder, and each chunk belongs to a new run.  COPY directly into
    # the target avoids copying every batch twice.  A duplicate can still
    # occur across chunk boundaries, so retain the established upsert path as
    # a transaction-local fallback.
    try:
        with conn.cursor() as cur:
            cur.execute("SAVEPOINT pending_fact_direct_copy")
            _copy_df(
                cur,
                pending_df[_PENDING_FACT_COLUMNS],
                "pending_fact_precios (run_id, producto_sk, sucursal_sk, fecha_id, "
                "precio_lista, precio_referencia, precio_promo1, precio_promo2, "
                "leyenda_promo1, leyenda_promo2, tiene_promo)",
            )
            cur.execute("RELEASE SAVEPOINT pending_fact_direct_copy")
    except Exception as exc:
        if _sqlstate(exc) != "23505":
            raise
        # psycopg3 closes a cursor after COPY fails, while psycopg2 does not.
        # Start a fresh cursor for the savepoint rollback so both drivers use
        # the same safe fallback.
        with conn.cursor() as cur:
            cur.execute("ROLLBACK TO SAVEPOINT pending_fact_direct_copy")
            cur.execute("RELEASE SAVEPOINT pending_fact_direct_copy")
            count = _load_pending_fact_with_upsert(cur, pending_df)
        log.info("warehouse.pending_fact_direct_copy_fallback", run_id=run_id)
    else:
        count = len(pending_df)

    log.info("warehouse.pending_fact_loaded", run_id=run_id, rows=count)
    return count


def _sqlstate(exc: Exception) -> str | None:
    """Return the portable SQLSTATE exposed by psycopg2 or psycopg3."""
    return getattr(exc, "sqlstate", None) or getattr(exc, "pgcode", None)


def _load_pending_fact_with_upsert(cur, pending_df: pd.DataFrame) -> int:
    """Load one exceptional duplicate-bearing chunk through the safe upsert path."""
    cur.execute(
        """
        CREATE TEMP TABLE tmp_pending_fact_precios (
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
        ) ON COMMIT PRESERVE ROWS
        """
    )
    # The write connection stays open through a fact stream.  Keep the
    # fallback's scratch relation and clear it between exceptional chunks.
    cur.execute("TRUNCATE tmp_pending_fact_precios")
    _copy_df(cur, pending_df[_PENDING_FACT_TEMP_COLUMNS], "tmp_pending_fact_precios")
    cur.execute(
        """
        INSERT INTO pending_fact_precios (
            run_id, producto_sk, sucursal_sk, fecha_id,
            precio_lista, precio_referencia,
            precio_promo1, precio_promo2,
            leyenda_promo1, leyenda_promo2,
            tiene_promo
        )
        SELECT run_id, producto_sk, sucursal_sk, fecha_id,
            precio_lista, precio_referencia,
            precio_promo1, precio_promo2,
            leyenda_promo1, leyenda_promo2,
            tiene_promo
        FROM tmp_pending_fact_precios
        ON CONFLICT (run_id, producto_sk, sucursal_sk, fecha_id) DO UPDATE SET
            precio_lista      = EXCLUDED.precio_lista,
            precio_referencia = EXCLUDED.precio_referencia,
            precio_promo1     = EXCLUDED.precio_promo1,
            precio_promo2     = EXCLUDED.precio_promo2,
            leyenda_promo1    = EXCLUDED.leyenda_promo1,
            leyenda_promo2    = EXCLUDED.leyenda_promo2,
            tiene_promo       = EXCLUDED.tiene_promo
        """
    )
    return cur.rowcount


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


def load_dim_produto_from_staging(
    conn, run_id: int, *, update_existing: bool = True
) -> int:
    """
    Load dim_produto directly from stg_produtos via SQL (no Python intermediary).

    Avoids loading 14M+ rows into Python memory: the DB does the deduplication
    and type-casting inline.  Returns the number of rows upserted.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO dim_producto (
                producto_identity, source_id_comercio, source_id_bandera,
                id_producto, es_ean,
                productos_descripcion, productos_marca,
                productos_cantidad_presentacion, productos_unidad_medida_presentacion,
                productos_cantidad_referencia,   productos_unidad_medida_referencia
            )
            SELECT DISTINCT ON (producto_identity)
                producto_identity,
                source_id_comercio,
                source_id_bandera,
                id_producto,
                es_ean,
                TRIM(productos_descripcion),
                TRIM(productos_marca),
                TRIM(productos_cantidad_presentacion),
                TRIM(productos_unidad_medida_presentacion),
                TRIM(productos_cantidad_referencia),
                TRIM(productos_unidad_medida_referencia)
            FROM (
                SELECT
                    TRIM(id_producto) AS id_producto,
                    (TRIM(productos_ean) = '1') AS es_ean,
                    CASE
                        WHEN TRIM(productos_ean) = '1'
                             AND pipeline_is_valid_ean(TRIM(id_producto))
                            THEN 'ean:' || TRIM(id_producto)
                        ELSE 'chain:' || id_comercio || ':' || id_bandera || ':' || TRIM(id_producto)
                    END AS producto_identity,
                    CASE
                        WHEN TRIM(productos_ean) = '1'
                             AND pipeline_is_valid_ean(TRIM(id_producto)) THEN NULL
                        ELSE TRIM(id_comercio)::INT
                    END AS source_id_comercio,
                    CASE
                        WHEN TRIM(productos_ean) = '1'
                             AND pipeline_is_valid_ean(TRIM(id_producto)) THEN NULL
                        ELSE TRIM(id_bandera)::INT
                    END AS source_id_bandera,
                    productos_descripcion,
                    productos_marca,
                    productos_cantidad_presentacion,
                    productos_unidad_medida_presentacion,
                    productos_cantidad_referencia,
                    productos_unidad_medida_referencia
                FROM stg_productos
                WHERE run_id = %s
                  AND TRIM(productos_ean) IN ('0', '1')
                  AND TRIM(id_comercio) ~ '^[0-9]+$'
                  AND TRIM(id_bandera) ~ '^[0-9]+$'
                  AND TRIM(id_sucursal) ~ '^[0-9]+$'
                  AND TRIM(id_producto) <> ''
                  AND TRIM(productos_descripcion) <> ''
            ) AS source_productos
            ORDER BY producto_identity
            ON CONFLICT (producto_identity) DO UPDATE SET
                es_ean                               = EXCLUDED.es_ean,
                productos_descripcion                = EXCLUDED.productos_descripcion,
                productos_marca                      = EXCLUDED.productos_marca,
                productos_cantidad_presentacion      = EXCLUDED.productos_cantidad_presentacion,
                productos_unidad_medida_presentacion = EXCLUDED.productos_unidad_medida_presentacion,
                productos_cantidad_referencia        = EXCLUDED.productos_cantidad_referencia,
                productos_unidad_medida_referencia   = EXCLUDED.productos_unidad_medida_referencia,
                updated_at                           = NOW()
            WHERE %s
              AND (
                  dim_producto.es_ean,
                  dim_producto.productos_descripcion,
                  dim_producto.productos_marca,
                  dim_producto.productos_cantidad_presentacion,
                  dim_producto.productos_unidad_medida_presentacion,
                  dim_producto.productos_cantidad_referencia,
                  dim_producto.productos_unidad_medida_referencia
              ) IS DISTINCT FROM (
                  EXCLUDED.es_ean,
                  EXCLUDED.productos_descripcion,
                  EXCLUDED.productos_marca,
                  EXCLUDED.productos_cantidad_presentacion,
                  EXCLUDED.productos_unidad_medida_presentacion,
                  EXCLUDED.productos_cantidad_referencia,
                  EXCLUDED.productos_unidad_medida_referencia
              )
            """,
            (run_id, update_existing),
        )
        count = cur.rowcount
    log.info("warehouse.dim_producto_loaded_from_staging", run_id=run_id, rows=count)
    return count


def load_dimensions_no_producto(
    conn,
    comercios: list[ComercioRow],
    sucursales: list[SucursalRow],
    *,
    update_existing: bool = True,
) -> tuple[int, int]:
    """Load comercio/sucursal dimensions, optionally preserving existing values."""
    rc = _load_dim_comercio(conn, comercios, update_existing=update_existing)
    rs = _load_dim_sucursal(conn, sucursales, update_existing=update_existing)
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
