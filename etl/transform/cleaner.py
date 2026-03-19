"""
cleaner.py — Type-cast and normalize staging rows into typed DataFrames.

Reads from stg_comercios / stg_sucursales / stg_productos (all VARCHAR) and
returns DataFrames with final types ready for loading into dimension and fact
tables.  Invalid rows are collected separately so callers can persist them to
``rejected_rows``.

Public API
----------
clean_comercios(df)  → CleanResult[ComercioRow]
clean_sucursales(df) → CleanResult[SucursalRow]
clean_productos(df)  → CleanResult[ProductoRow]
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Generic, Optional, TypeVar

import pandas as pd
import structlog

log = structlog.get_logger(__name__)

# ── Coordinate bounds (Argentina bounding box) ────────────────────────────────

_LAT_MIN, _LAT_MAX = -55.0, -21.0
_LON_MIN, _LON_MAX = -73.5, -53.5

# ── Valid provincia ISO codes ─────────────────────────────────────────────────

_VALID_PROVINCIAS = {
    "AR-B", "AR-C", "AR-D", "AR-E", "AR-F", "AR-G", "AR-H",
    "AR-J", "AR-K", "AR-L", "AR-M", "AR-N", "AR-P", "AR-Q",
    "AR-R", "AR-S", "AR-T", "AR-U", "AR-V", "AR-W", "AR-X",
    "AR-Y", "AR-Z",
}

# ── Result container ──────────────────────────────────────────────────────────

T = TypeVar("T")


@dataclass
class CleanResult(Generic[T]):
    clean: list[T] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)  # {row_data, reason}


# ── Typed row containers ──────────────────────────────────────────────────────


@dataclass
class ComercioRow:
    id_comercio: int
    id_bandera: int
    comercio_cuit: str
    comercio_razon_social: str
    comercio_bandera_nombre: str
    comercio_bandera_url: str
    comercio_ultima_actualizacion: Optional[str]
    comercio_version_sepa: str


@dataclass
class SucursalRow:
    id_comercio: int
    id_bandera: int
    id_sucursal: int
    sucursales_nombre: str
    sucursales_tipo: str
    sucursales_calle: Optional[str]
    sucursales_numero: Optional[str]
    sucursales_latitud: Optional[Decimal]
    sucursales_longitud: Optional[Decimal]
    sucursales_observaciones: Optional[str]
    sucursales_barrio: Optional[str]
    sucursales_codigo_postal: Optional[str]
    sucursales_localidad: str
    sucursales_provincia: str
    sucursales_lunes_horario_atencion: Optional[str]
    sucursales_martes_horario_atencion: Optional[str]
    sucursales_miercoles_horario_atencion: Optional[str]
    sucursales_jueves_horario_atencion: Optional[str]
    sucursales_viernes_horario_atencion: Optional[str]
    sucursales_sabado_horario_atencion: Optional[str]
    sucursales_domingo_horario_atencion: Optional[str]


@dataclass
class ProductoRow:
    id_comercio: int
    id_bandera: int
    id_sucursal: int
    id_producto: str
    es_ean: bool
    productos_descripcion: str
    productos_cantidad_presentacion: str
    productos_unidad_medida_presentacion: str
    productos_marca: str
    productos_precio_lista: Decimal
    productos_precio_referencia: Decimal
    productos_cantidad_referencia: str
    productos_unidad_medida_referencia: str
    productos_precio_unitario_promo1: Optional[Decimal]
    productos_leyenda_promo1: Optional[str]
    productos_precio_unitario_promo2: Optional[Decimal]
    productos_leyenda_promo2: Optional[str]
    tiene_promo: bool


# ── Helpers ───────────────────────────────────────────────────────────────────


def _str(val: object) -> str:
    """Strip and return string; empty string if None/NaN."""
    if val is None:
        return ""
    s = str(val).strip()
    return "" if s.lower() in ("nan", "none", "") else s


def _opt_str(val: object) -> Optional[str]:
    s = _str(val)
    return s if s else None


def _int(val: object, field_name: str) -> int:
    s = _str(val)
    try:
        return int(s)
    except (ValueError, TypeError):
        raise ValueError(f"{field_name}: cannot cast {val!r} to int")


def _decimal(val: object, field_name: str) -> Decimal:
    s = _str(val)
    try:
        d = Decimal(s)
        if not math.isfinite(float(d)):
            raise ValueError("non-finite")
        return d
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(f"{field_name}: cannot cast {val!r} to Decimal")


def _opt_decimal(val: object, field_name: str) -> Optional[Decimal]:
    s = _str(val)
    if not s:
        return None
    return _decimal(s, field_name)


def _coord(val: object, field_name: str, lo: float, hi: float) -> Optional[Decimal]:
    """Cast coordinate string → Decimal and validate range; returns None if blank."""
    d = _opt_decimal(val, field_name)
    if d is None:
        return None
    f = float(d)
    if not (lo <= f <= hi):
        raise ValueError(
            f"{field_name}: {f} out of range [{lo}, {hi}]"
        )
    return d


# ── Public clean functions ────────────────────────────────────────────────────


def clean_comercios(df: pd.DataFrame) -> CleanResult[ComercioRow]:
    """Cast and validate stg_comercios rows."""
    result: CleanResult[ComercioRow] = CleanResult()

    for _, row in df.iterrows():
        raw = row.to_dict()
        try:
            record = ComercioRow(
                id_comercio=_int(raw.get("id_comercio"), "id_comercio"),
                id_bandera=_int(raw.get("id_bandera"), "id_bandera"),
                comercio_cuit=_str(raw.get("comercio_cuit")),
                comercio_razon_social=_str(raw.get("comercio_razon_social")),
                comercio_bandera_nombre=_str(raw.get("comercio_bandera_nombre")),
                comercio_bandera_url=_str(raw.get("comercio_bandera_url")),
                comercio_ultima_actualizacion=_opt_str(raw.get("comercio_ultima_actualizacion")),
                comercio_version_sepa=_str(raw.get("comercio_version_sepa")),
            )
            result.clean.append(record)
        except (ValueError, KeyError) as exc:
            result.rejected.append({"row": raw, "reason": str(exc)})

    _log_summary("comercios", result)
    return result


def clean_sucursales(df: pd.DataFrame) -> CleanResult[SucursalRow]:
    """Cast, validate coordinates and provincia for stg_sucursales rows."""
    result: CleanResult[SucursalRow] = CleanResult()

    for _, row in df.iterrows():
        raw = row.to_dict()
        try:
            provincia = _str(raw.get("sucursales_provincia"))
            if provincia not in _VALID_PROVINCIAS:
                raise ValueError(
                    f"sucursales_provincia: {provincia!r} is not a valid AR-* ISO 3166-2 code"
                )

            record = SucursalRow(
                id_comercio=_int(raw.get("id_comercio"), "id_comercio"),
                id_bandera=_int(raw.get("id_bandera"), "id_bandera"),
                id_sucursal=_int(raw.get("id_sucursal"), "id_sucursal"),
                sucursales_nombre=_str(raw.get("sucursales_nombre")),
                sucursales_tipo=_str(raw.get("sucursales_tipo")),
                sucursales_calle=_opt_str(raw.get("sucursales_calle")),
                sucursales_numero=_opt_str(raw.get("sucursales_numero")),
                sucursales_latitud=_coord(
                    raw.get("sucursales_latitud"), "sucursales_latitud", _LAT_MIN, _LAT_MAX
                ),
                sucursales_longitud=_coord(
                    raw.get("sucursales_longitud"), "sucursales_longitud", _LON_MIN, _LON_MAX
                ),
                sucursales_observaciones=_opt_str(raw.get("sucursales_observaciones")),
                sucursales_barrio=_opt_str(raw.get("sucursales_barrio")),
                sucursales_codigo_postal=_opt_str(raw.get("sucursales_codigo_postal")),
                sucursales_localidad=_str(raw.get("sucursales_localidad")),
                sucursales_provincia=provincia,
                sucursales_lunes_horario_atencion=_opt_str(raw.get("sucursales_lunes_horario_atencion")),
                sucursales_martes_horario_atencion=_opt_str(raw.get("sucursales_martes_horario_atencion")),
                sucursales_miercoles_horario_atencion=_opt_str(raw.get("sucursales_miercoles_horario_atencion")),
                sucursales_jueves_horario_atencion=_opt_str(raw.get("sucursales_jueves_horario_atencion")),
                sucursales_viernes_horario_atencion=_opt_str(raw.get("sucursales_viernes_horario_atencion")),
                sucursales_sabado_horario_atencion=_opt_str(raw.get("sucursales_sabado_horario_atencion")),
                sucursales_domingo_horario_atencion=_opt_str(raw.get("sucursales_domingo_horario_atencion")),
            )
            result.clean.append(record)
        except (ValueError, KeyError) as exc:
            result.rejected.append({"row": raw, "reason": str(exc)})

    _log_summary("sucursales", result)
    return result


def clean_productos(df: pd.DataFrame) -> CleanResult[ProductoRow]:
    """Cast prices, normalize es_ean flag, and derive tiene_promo for stg_productos rows."""
    result: CleanResult[ProductoRow] = CleanResult()

    for _, row in df.iterrows():
        raw = row.to_dict()
        try:
            promo1 = _opt_decimal(raw.get("productos_precio_unitario_promo1"), "productos_precio_unitario_promo1")
            promo2 = _opt_decimal(raw.get("productos_precio_unitario_promo2"), "productos_precio_unitario_promo2")

            ean_raw = _str(raw.get("productos_ean"))
            if ean_raw == "1":
                es_ean = True
            elif ean_raw == "0":
                es_ean = False
            else:
                raise ValueError(f"productos_ean: expected '0' or '1', got {ean_raw!r}")

            precio_lista = _decimal(raw.get("productos_precio_lista"), "productos_precio_lista")
            precio_referencia = _decimal(raw.get("productos_precio_referencia"), "productos_precio_referencia")

            if float(precio_lista) <= 0:
                raise ValueError(f"productos_precio_lista must be > 0, got {precio_lista}")
            if float(precio_referencia) <= 0:
                raise ValueError(f"productos_precio_referencia must be > 0, got {precio_referencia}")

            record = ProductoRow(
                id_comercio=_int(raw.get("id_comercio"), "id_comercio"),
                id_bandera=_int(raw.get("id_bandera"), "id_bandera"),
                id_sucursal=_int(raw.get("id_sucursal"), "id_sucursal"),
                id_producto=_str(raw.get("id_producto")),
                es_ean=es_ean,
                productos_descripcion=_str(raw.get("productos_descripcion")),
                productos_cantidad_presentacion=_str(raw.get("productos_cantidad_presentacion")),
                productos_unidad_medida_presentacion=_str(raw.get("productos_unidad_medida_presentacion")),
                productos_marca=_str(raw.get("productos_marca")),
                productos_precio_lista=precio_lista,
                productos_precio_referencia=precio_referencia,
                productos_cantidad_referencia=_str(raw.get("productos_cantidad_referencia")),
                productos_unidad_medida_referencia=_str(raw.get("productos_unidad_medida_referencia")),
                productos_precio_unitario_promo1=promo1,
                productos_leyenda_promo1=_opt_str(raw.get("productos_leyenda_promo1")),
                productos_precio_unitario_promo2=promo2,
                productos_leyenda_promo2=_opt_str(raw.get("productos_leyenda_promo2")),
                tiene_promo=promo1 is not None or promo2 is not None,
            )
            result.clean.append(record)
        except (ValueError, KeyError) as exc:
            result.rejected.append({"row": raw, "reason": str(exc)})

    _log_summary("productos", result)
    return result


# ── Logging helper ────────────────────────────────────────────────────────────


def _log_summary(entity: str, result: CleanResult) -> None:
    log.info(
        "cleaner.summary",
        entity=entity,
        clean=len(result.clean),
        rejected=len(result.rejected),
    )
    if result.rejected:
        log.warning(
            "cleaner.rejected_rows",
            entity=entity,
            count=len(result.rejected),
            sample_reasons=[r["reason"] for r in result.rejected[:3]],
        )
