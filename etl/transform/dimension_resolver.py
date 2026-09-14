"""
dimension_resolver.py — Resolve natural keys → surrogate keys for all dimensions.

Each resolver follows SCD Type 1: INSERT the row if it doesn't exist, UPDATE
attributes if it does (last-write-wins).  All operations are idempotent and
use ON CONFLICT so they are safe to retry.

Public API
----------
resolve_comercio(conn, row)    → comercio_sk (int)
resolve_sucursal(conn, row)    → sucursal_sk (int)
resolve_producto(conn, row)    → producto_sk (int)
resolve_provincia(conn, code)  → provincia_sk (int)
resolve_fecha(conn, date)      → fecha_id (int, YYYYMMDD)
"""

from __future__ import annotations

from datetime import date
from typing import Optional

import psycopg2
import structlog

from etl.transform.cleaner import ComercioRow, ProductoRow, SucursalRow
from etl.transform.product_identity import product_identity

log = structlog.get_logger(__name__)


# ── comercio ──────────────────────────────────────────────────────────────────


def resolve_comercio(
    conn: "psycopg2.connection",
    row: ComercioRow,
) -> int:
    """
    Upsert into dim_comercio and return comercio_sk.

    Natural key: (id_comercio, id_bandera).
    SCD Type 1: attributes are overwritten on conflict.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO dim_comercio
                (id_comercio, id_bandera,
                 comercio_cuit, comercio_razon_social,
                 comercio_bandera_nombre, comercio_bandera_url,
                 updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (id_comercio, id_bandera) DO UPDATE SET
                comercio_cuit           = EXCLUDED.comercio_cuit,
                comercio_razon_social   = EXCLUDED.comercio_razon_social,
                comercio_bandera_nombre = EXCLUDED.comercio_bandera_nombre,
                comercio_bandera_url    = EXCLUDED.comercio_bandera_url,
                updated_at              = NOW()
            RETURNING comercio_sk
            """,
            (
                row.id_comercio,
                row.id_bandera,
                row.comercio_cuit or None,
                row.comercio_razon_social or None,
                row.comercio_bandera_nombre or None,
                row.comercio_bandera_url or None,
            ),
        )
        (sk,) = cur.fetchone()
    log.debug(
        "dim.comercio.resolved",
        id_comercio=row.id_comercio,
        id_bandera=row.id_bandera,
        comercio_sk=sk,
    )
    return sk


# ── provincia ─────────────────────────────────────────────────────────────────


def resolve_provincia(
    conn: "psycopg2.connection",
    codigo_iso: str,
) -> Optional[int]:
    """
    Look up provincia_sk by ISO code.  dim_provincia is pre-seeded and
    read-only from the pipeline; returns None if the code is not found.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT provincia_sk FROM dim_provincia WHERE codigo_iso = %s",
            (codigo_iso,),
        )
        result = cur.fetchone()

    if result is None:
        log.warning("dim.provincia.not_found", codigo_iso=codigo_iso)
        return None

    sk: int = result[0]
    log.debug("dim.provincia.resolved", codigo_iso=codigo_iso, provincia_sk=sk)
    return sk


# ── sucursal ──────────────────────────────────────────────────────────────────


def resolve_sucursal(
    conn: "psycopg2.connection",
    row: SucursalRow,
    comercio_sk: int,
    provincia_sk: Optional[int],
) -> int:
    """
    Upsert into dim_sucursal and return sucursal_sk.

    Natural key: (id_comercio, id_bandera, id_sucursal).
    SCD Type 1: lat/lon, tipo, address attributes are overwritten on conflict.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO dim_sucursal
                (id_comercio, id_bandera, id_sucursal,
                 comercio_sk, provincia_sk,
                 sucursales_nombre, sucursales_tipo,
                 sucursales_calle, sucursales_numero,
                 sucursales_barrio, sucursales_codigo_postal,
                 sucursales_localidad,
                 sucursales_latitud, sucursales_longitud,
                 updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (id_comercio, id_bandera, id_sucursal) DO UPDATE SET
                comercio_sk             = EXCLUDED.comercio_sk,
                provincia_sk            = EXCLUDED.provincia_sk,
                sucursales_nombre       = EXCLUDED.sucursales_nombre,
                sucursales_tipo         = EXCLUDED.sucursales_tipo,
                sucursales_calle        = EXCLUDED.sucursales_calle,
                sucursales_numero       = EXCLUDED.sucursales_numero,
                sucursales_barrio       = EXCLUDED.sucursales_barrio,
                sucursales_codigo_postal = EXCLUDED.sucursales_codigo_postal,
                sucursales_localidad    = EXCLUDED.sucursales_localidad,
                sucursales_latitud      = EXCLUDED.sucursales_latitud,
                sucursales_longitud     = EXCLUDED.sucursales_longitud,
                updated_at              = NOW()
            RETURNING sucursal_sk
            """,
            (
                row.id_comercio,
                row.id_bandera,
                row.id_sucursal,
                comercio_sk,
                provincia_sk,
                row.sucursales_nombre or None,
                row.sucursales_tipo or None,
                row.sucursales_calle,
                row.sucursales_numero,
                row.sucursales_barrio,
                row.sucursales_codigo_postal,
                row.sucursales_localidad or None,
                row.sucursales_latitud,
                row.sucursales_longitud,
            ),
        )
        (sk,) = cur.fetchone()
    log.debug(
        "dim.sucursal.resolved",
        id_comercio=row.id_comercio,
        id_bandera=row.id_bandera,
        id_sucursal=row.id_sucursal,
        sucursal_sk=sk,
    )
    return sk


# ── producto ──────────────────────────────────────────────────────────────────


def resolve_producto(
    conn: "psycopg2.connection",
    row: ProductoRow,
) -> int:
    """
    Upsert into dim_producto and return producto_sk.

    Valid EANs are shared globally. Other source codes are scoped to the
    reporting retailer.
    SCD Type 1: descriptive attributes are overwritten on conflict.
    """
    identity = product_identity(row)
    retailer_scoped = identity.startswith("chain:")
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO dim_producto
                (producto_identity, source_id_comercio, source_id_bandera,
                 id_producto, es_ean,
                 productos_descripcion, productos_marca,
                 productos_cantidad_presentacion,
                 productos_unidad_medida_presentacion,
                 productos_cantidad_referencia,
                 productos_unidad_medida_referencia,
                 updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (producto_identity) DO UPDATE SET
                es_ean                              = EXCLUDED.es_ean,
                productos_descripcion               = EXCLUDED.productos_descripcion,
                productos_marca                     = EXCLUDED.productos_marca,
                productos_cantidad_presentacion     = EXCLUDED.productos_cantidad_presentacion,
                productos_unidad_medida_presentacion = EXCLUDED.productos_unidad_medida_presentacion,
                productos_cantidad_referencia       = EXCLUDED.productos_cantidad_referencia,
                productos_unidad_medida_referencia  = EXCLUDED.productos_unidad_medida_referencia,
                updated_at                          = NOW()
            RETURNING producto_sk
            """,
            (
                identity,
                row.id_comercio if retailer_scoped else None,
                row.id_bandera if retailer_scoped else None,
                row.id_producto,
                row.es_ean,
                row.productos_descripcion or None,
                row.productos_marca or None,
                row.productos_cantidad_presentacion or None,
                row.productos_unidad_medida_presentacion or None,
                row.productos_cantidad_referencia or None,
                row.productos_unidad_medida_referencia or None,
            ),
        )
        (sk,) = cur.fetchone()
    log.debug("dim.producto.resolved", id_producto=row.id_producto, producto_sk=sk)
    return sk


# ── fecha ─────────────────────────────────────────────────────────────────────


def resolve_fecha(
    conn: "psycopg2.connection",
    d: date,
) -> int:
    """
    Return the fecha_id (YYYYMMDD int) for *d*.

    dim_fecha is pre-seeded for the full horizon; this function inserts the
    row if missing (defensive, covers dates outside the seeded range) and
    always returns the fecha_id.
    """
    fecha_id = int(d.strftime("%Y%m%d"))
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO dim_fecha
                (fecha_id, fecha, anio, mes, trimestre,
                 semana_iso, dia_semana, es_fin_semana)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (fecha_id) DO NOTHING
            """,
            (
                fecha_id,
                d,
                d.year,
                d.month,
                (d.month - 1) // 3 + 1,
                d.isocalendar()[1],
                d.isoweekday(),          # 1=Monday … 7=Sunday (ISO)
                d.isoweekday() >= 6,
            ),
        )
    log.debug("dim.fecha.resolved", fecha_id=fecha_id)
    return fecha_id
