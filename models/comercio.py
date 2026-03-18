"""ComercioRecord — Pydantic v2 model for comercio.csv rows."""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, field_validator


class ComercioRecord(BaseModel):
    id_comercio: int
    id_bandera: int
    comercio_cuit: str
    comercio_razon_social: str
    comercio_bandera_nombre: str
    comercio_bandera_url: str
    comercio_ultima_actualizacion: datetime
    comercio_version_sepa: str

    model_config = {"str_strip_whitespace": True}

    @field_validator("comercio_cuit")
    @classmethod
    def cuit_digits_only(cls, v: str) -> str:
        digits = v.replace("-", "")
        if not digits.isdigit() or len(digits) != 11:
            raise ValueError(f"CUIT must be 11 digits, got: {v!r}")
        return digits
