"""
fact_builder.py — Resolve surrogate keys and build the fact_precios DataFrame.

Takes the typed rows produced by cleaner.py, resolves every foreign key
through dimension_resolver.py (with an in-memory cache per call to avoid
repeated round-trips for the same natural key), and returns a DataFrame
whose columns match fact_precios exactly.

Public API
----------
build_fact_df(conn, productos, sucursales, comercios, run_date, run_id)
    -> FactBuildResult
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

import pandas as pd
import structlog

from etl.transform.cleaner import ComercioRow, ProductoRow, SucursalRow
from etl.transform.dimension_resolver import (
    resolve_comercio,
    resolve_fecha,
    resolve_producto,
    resolve_provincia,
    resolve_sucursal,
)

log = structlog.get_logger(__name__)

# Chunk size for streaming produto processing (300k rows ≈ ~200MB peak)
PRODUTO_CHUNK_SIZE = 300_000

# Column order must match fact_precios
_FACT_COLUMNS = [
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


@dataclass
class FactBuildResult:
    df: pd.DataFrame                   # rows ready for fact_precios
    rejected: list[dict] = field(default_factory=list)  # {row, reason}


def build_fact_df(
    conn: "psycopg2.connection",
    productos: list[ProductoRow],
    sucursales: list[SucursalRow],
    comercios: list[ComercioRow],
    run_date: date,
    run_id: int,
) -> FactBuildResult:
    """
    Resolve all surrogate keys and return a DataFrame ready for fact_precios.

    Parameters
    ----------
    conn:
        Open psycopg2 connection. Caller manages transaction scope.
    productos:
        Typed product rows from cleaner.clean_productos().
    sucursales:
        Typed branch rows from cleaner.clean_sucursales(); used to upsert
        dim_sucursal before resolving sucursal_sk.
    comercios:
        Typed comercio rows from cleaner.clean_comercios(); used to upsert
        dim_comercio before resolving comercio_sk.
    run_date:
        The calendar date this ingestion run represents (used for fecha_id).
    run_id:
        ingestion_runs.id for this pipeline run.

    Returns
    -------
    FactBuildResult with a DataFrame of resolved rows and any rejected rows.
    """
    logger = log.bind(run_id=run_id, run_date=str(run_date))
    logger.info("fact_builder.start", n_productos=len(productos))

    # ── 1. Resolve fecha (single value for the whole run) ─────────────────────
    fecha_id = resolve_fecha(conn, run_date)

    # ── 2. Cache comercio_sk by (id_comercio, id_bandera) ────────────────────
    comercio_cache: dict[tuple[int, int], int] = {}
    for c in comercios:
        key = (c.id_comercio, c.id_bandera)
        if key not in comercio_cache:
            comercio_cache[key] = resolve_comercio(conn, c)

    logger.info("fact_builder.comercios_resolved", count=len(comercio_cache))

    # ── 3. Cache provincia_sk by ISO code ────────────────────────────────────
    provincia_cache: dict[str, Optional[int]] = {}
    for s in sucursales:
        code = s.sucursales_provincia
        if code not in provincia_cache:
            provincia_cache[code] = resolve_provincia(conn, code)

    # ── 4. Cache sucursal_sk by (id_comercio, id_bandera, id_sucursal) ───────
    sucursal_cache: dict[tuple[int, int, int], int] = {}
    sucursal_index: dict[tuple[int, int, int], SucursalRow] = {
        (s.id_comercio, s.id_bandera, s.id_sucursal): s for s in sucursales
    }
    for key, s in sucursal_index.items():
        comercio_sk = comercio_cache.get((s.id_comercio, s.id_bandera))
        if comercio_sk is None:
            log.warning(
                "fact_builder.sucursal_missing_comercio",
                id_comercio=s.id_comercio,
                id_bandera=s.id_bandera,
            )
            continue
        provincia_sk = provincia_cache.get(s.sucursales_provincia)
        sucursal_cache[key] = resolve_sucursal(conn, s, comercio_sk, provincia_sk)

    logger.info("fact_builder.sucursales_resolved", count=len(sucursal_cache))

    # ── 5. Cache producto_sk by id_producto ──────────────────────────────────
    producto_cache: dict[str, int] = {}
    # deduplicate: same id_producto can appear many times across branches
    seen_productos: dict[str, ProductoRow] = {}
    for p in productos:
        seen_productos.setdefault(p.id_producto, p)
    for id_producto, p in seen_productos.items():
        producto_cache[id_producto] = resolve_producto(conn, p)

    logger.info("fact_builder.productos_resolved", count=len(producto_cache))

    # ── 6. Build fact rows ────────────────────────────────────────────────────
    rows: list[dict] = []
    rejected: list[dict] = []

    for p in productos:
        sucursal_key = (p.id_comercio, p.id_bandera, p.id_sucursal)
        sucursal_sk = sucursal_cache.get(sucursal_key)
        if sucursal_sk is None:
            rejected.append({
                "row": p.__dict__,
                "reason": (
                    f"sucursal_sk not found for "
                    f"(id_comercio={p.id_comercio}, id_bandera={p.id_bandera}, "
                    f"id_sucursal={p.id_sucursal})"
                ),
            })
            continue

        producto_sk = producto_cache.get(p.id_producto)
        if producto_sk is None:
            rejected.append({
                "row": p.__dict__,
                "reason": f"producto_sk not found for id_producto={p.id_producto!r}",
            })
            continue

        rows.append({
            "producto_sk":      producto_sk,
            "sucursal_sk":      sucursal_sk,
            "fecha_id":         fecha_id,
            "run_id":           run_id,
            "precio_lista":     p.productos_precio_lista,
            "precio_referencia": p.productos_precio_referencia,
            "precio_promo1":    p.productos_precio_unitario_promo1,
            "precio_promo2":    p.productos_precio_unitario_promo2,
            "leyenda_promo1":   p.productos_leyenda_promo1,
            "leyenda_promo2":   p.productos_leyenda_promo2,
            "tiene_promo":      p.tiene_promo,
        })

    df = pd.DataFrame(rows, columns=_FACT_COLUMNS) if rows else pd.DataFrame(columns=_FACT_COLUMNS)

    logger.info(
        "fact_builder.done",
        fact_rows=len(df),
        rejected=len(rejected),
    )

    if rejected:
        log.warning(
            "fact_builder.rejected_rows",
            count=len(rejected),
            sample_reasons=[r["reason"] for r in rejected[:3]],
        )

    return FactBuildResult(df=df, rejected=rejected)


# ---------------------------------------------------------------------------
# Streaming / chunked helpers
# ---------------------------------------------------------------------------


def build_sk_caches(
    conn: "psycopg2.connection",
    run_date: date,
) -> tuple[int, dict, dict, dict]:
    """
    Build surrogate-key lookup caches by reading dimension tables directly.

    Must be called *after* all three dimension tables have been loaded.
    Returns ``(fecha_id, comercio_cache, sucursal_cache, produto_cache)``.
    The caches are small (number of unique comercios/sucursais/produtos) and
    fit comfortably in memory.
    """
    fecha_id = resolve_fecha(conn, run_date)

    with conn.cursor() as cur:
        cur.execute("SELECT id_comercio, id_bandera, comercio_sk FROM dim_comercio")
        comercio_cache: dict[tuple[int, int], int] = {
            (r[0], r[1]): r[2] for r in cur.fetchall()
        }

        cur.execute(
            "SELECT id_comercio, id_bandera, id_sucursal, sucursal_sk FROM dim_sucursal"
        )
        sucursal_cache: dict[tuple[int, int, int], int] = {
            (r[0], r[1], r[2]): r[3] for r in cur.fetchall()
        }

        cur.execute("SELECT id_producto, producto_sk FROM dim_producto")
        producto_cache: dict[str, int] = {r[0]: r[1] for r in cur.fetchall()}

    log.info(
        "fact_builder.caches_built",
        comercios=len(comercio_cache),
        sucursales=len(sucursal_cache),
        productos=len(producto_cache),
    )
    return fecha_id, comercio_cache, sucursal_cache, producto_cache


def build_fact_chunk(
    productos: list[ProductoRow],
    comercio_cache: dict[tuple[int, int], int],
    sucursal_cache: dict[tuple[int, int, int], int],
    producto_cache: dict[str, int],
    fecha_id: int,
    run_id: int,
) -> FactBuildResult:
    """
    Resolve surrogate keys for a *chunk* of productos using pre-built caches.

    Unlike ``build_fact_df``, this function does not touch the database —
    all lookups are dictionary reads against the in-memory caches.  Call
    ``build_sk_caches`` once before iterating chunks to populate them.
    """
    rows: list[dict] = []
    rejected: list[dict] = []

    for p in productos:
        sucursal_key = (p.id_comercio, p.id_bandera, p.id_sucursal)
        sucursal_sk = sucursal_cache.get(sucursal_key)
        if sucursal_sk is None:
            rejected.append({
                "row": p.__dict__,
                "reason": (
                    f"sucursal_sk not found for "
                    f"(id_comercio={p.id_comercio}, id_bandera={p.id_bandera}, "
                    f"id_sucursal={p.id_sucursal})"
                ),
            })
            continue

        producto_sk = producto_cache.get(p.id_producto)
        if producto_sk is None:
            rejected.append({
                "row": p.__dict__,
                "reason": f"producto_sk not found for id_producto={p.id_producto!r}",
            })
            continue

        rows.append({
            "producto_sk":      producto_sk,
            "sucursal_sk":      sucursal_sk,
            "fecha_id":         fecha_id,
            "run_id":           run_id,
            "precio_lista":     p.productos_precio_lista,
            "precio_referencia": p.productos_precio_referencia,
            "precio_promo1":    p.productos_precio_unitario_promo1,
            "precio_promo2":    p.productos_precio_unitario_promo2,
            "leyenda_promo1":   p.productos_leyenda_promo1,
            "leyenda_promo2":   p.productos_leyenda_promo2,
            "tiene_promo":      p.tiene_promo,
        })

    df = pd.DataFrame(rows, columns=_FACT_COLUMNS) if rows else pd.DataFrame(columns=_FACT_COLUMNS)
    return FactBuildResult(df=df, rejected=rejected)
