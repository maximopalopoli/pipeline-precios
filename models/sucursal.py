"""SucursalRecord — Pydantic v2 model for sucursales.csv rows."""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, field_validator


class TipoSucursal(str, Enum):
    hipermercado = "Hipermercado"
    supermercado = "Supermercado"
    autoservicio = "Autoservicio"
    tradicional = "Tradicional"
    web = "Web"


class SucursalRecord(BaseModel):
    id_comercio: int
    id_bandera: int
    id_sucursal: int
    sucursales_nombre: str
    sucursales_tipo: TipoSucursal
    sucursales_calle: Optional[str] = None
    sucursales_numero: Optional[str] = None
    sucursales_latitud: Optional[float] = None
    sucursales_longitud: Optional[float] = None
    sucursales_observaciones: Optional[str] = None
    sucursales_barrio: Optional[str] = None
    sucursales_codigo_postal: Optional[str] = None
    sucursales_localidad: str
    sucursales_provincia: str  # ISO 3166-2, e.g. "AR-B"
    sucursales_lunes_horario_atencion: Optional[str] = None
    sucursales_martes_horario_atencion: Optional[str] = None
    sucursales_miercoles_horario_atencion: Optional[str] = None
    sucursales_jueves_horario_atencion: Optional[str] = None
    sucursales_viernes_horario_atencion: Optional[str] = None
    sucursales_sabado_horario_atencion: Optional[str] = None
    sucursales_domingo_horario_atencion: Optional[str] = None

    model_config = {"str_strip_whitespace": True}

    @field_validator("sucursales_provincia")
    @classmethod
    def valid_iso_province(cls, v: str) -> str:
        if not v.startswith("AR-"):
            raise ValueError(f"Province must be an AR-* ISO 3166-2 code, got: {v!r}")
        return v
