"""ProductoRecord — Pydantic v2 model for productos.csv rows."""

from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class ProductoRecord(BaseModel):
    id_comercio: int
    id_bandera: int
    id_sucursal: int
    id_producto: str  # 13-digit EAN or zero-padded internal code
    productos_ean: bool  # "1" = standard EAN, "0" = internal code
    productos_descripcion: str
    productos_cantidad_presentacion: int
    productos_unidad_medida_presentacion: str
    productos_marca: str
    productos_precio_lista: float = Field(gt=0)
    productos_precio_referencia: float = Field(gt=0)
    productos_cantidad_referencia: int
    productos_unidad_medida_referencia: str
    productos_precio_unitario_promo1: Optional[float] = None
    productos_leyenda_promo1: Optional[str] = None
    productos_precio_unitario_promo2: Optional[float] = None
    productos_leyenda_promo2: Optional[str] = None

    model_config = {"str_strip_whitespace": True}

    @field_validator("id_producto")
    @classmethod
    def thirteen_digits(cls, v: str) -> str:
        if not v.isdigit() or len(v) != 13:
            raise ValueError(f"id_producto must be exactly 13 digits, got: {v!r}")
        return v

    @field_validator("productos_ean", mode="before")
    @classmethod
    def parse_ean_flag(cls, v: object) -> bool:
        if isinstance(v, str):
            if v == "1":
                return True
            if v == "0":
                return False
            raise ValueError(f"productos_ean must be '0' or '1', got: {v!r}")
        return bool(v)

    @model_validator(mode="after")
    def promo_prices_positive(self) -> "ProductoRecord":
        if self.productos_precio_unitario_promo1 is not None and self.productos_precio_unitario_promo1 <= 0:
            raise ValueError("productos_precio_unitario_promo1 must be > 0")
        if self.productos_precio_unitario_promo2 is not None and self.productos_precio_unitario_promo2 <= 0:
            raise ValueError("productos_precio_unitario_promo2 must be > 0")
        return self
