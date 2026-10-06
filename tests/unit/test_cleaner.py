"""Unit tests for etl/transform/cleaner.py."""

from decimal import Decimal

import pandas as pd
import pytest

from etl.quality.checks import _VALID_PROVINCIAS as QUALITY_PROVINCIAS
from etl.transform.cleaner import (
    clean_comercios,
    clean_productos,
    clean_sucursales,
)
from tests.price_cases import PRICE_CASES


# ── Helpers ───────────────────────────────────────────────────────────────────


def _comercio(**overrides):
    base = {
        "id_comercio": "1",
        "id_bandera": "2",
        "comercio_cuit": "20123456789",
        "comercio_razon_social": "Supermercado SA",
        "comercio_bandera_nombre": "SuperBrand",
        "comercio_bandera_url": "http://example.com",
        "comercio_ultima_actualizacion": "2024-01-15",
        "comercio_version_sepa": "1.0",
    }
    base.update(overrides)
    return base


def _sucursal(**overrides):
    base = {
        "id_comercio": "1",
        "id_bandera": "2",
        "id_sucursal": "10",
        "sucursales_nombre": "Sucursal Centro",
        "sucursales_tipo": "Supermercado",
        "sucursales_calle": "Av. Corrientes",
        "sucursales_numero": "1234",
        "sucursales_latitud": "-34.603722",
        "sucursales_longitud": "-58.381592",
        "sucursales_observaciones": "",
        "sucursales_barrio": "San Nicolás",
        "sucursales_codigo_postal": "1043",
        "sucursales_localidad": "Buenos Aires",
        "sucursales_provincia": "AR-C",
        "sucursales_lunes_horario_atencion": "09:00-21:00",
        "sucursales_martes_horario_atencion": "",
        "sucursales_miercoles_horario_atencion": "",
        "sucursales_jueves_horario_atencion": "",
        "sucursales_viernes_horario_atencion": "",
        "sucursales_sabado_horario_atencion": "",
        "sucursales_domingo_horario_atencion": "",
    }
    base.update(overrides)
    return base


def _producto(**overrides):
    base = {
        "id_comercio": "1",
        "id_bandera": "2",
        "id_sucursal": "10",
        "id_producto": "7790001234567",
        "productos_ean": "1",
        "productos_descripcion": "Leche entera 1L",
        "productos_cantidad_presentacion": "1",
        "productos_unidad_medida_presentacion": "L",
        "productos_marca": "La Serenísima",
        "productos_precio_lista": "250.50",
        "productos_precio_referencia": "240.00",
        "productos_cantidad_referencia": "1",
        "productos_unidad_medida_referencia": "L",
        "productos_precio_unitario_promo1": "",
        "productos_leyenda_promo1": "",
        "productos_precio_unitario_promo2": "",
        "productos_leyenda_promo2": "",
    }
    base.update(overrides)
    return base


def _df(*rows):
    return pd.DataFrame(list(rows))


# ── clean_comercios ───────────────────────────────────────────────────────────


class TestCleanComercios:
    def test_happy_path(self):
        r = clean_comercios(_df(_comercio()))
        assert len(r.clean) == 1
        assert len(r.rejected) == 0
        row = r.clean[0]
        assert row.id_comercio == 1
        assert row.id_bandera == 2

    def test_bad_id_comercio_rejected(self):
        r = clean_comercios(_df(_comercio(id_comercio="abc")))
        assert len(r.clean) == 0
        assert len(r.rejected) == 1
        assert "id_comercio" in r.rejected[0]["reason"]

    def test_multiple_rows_mixed(self):
        r = clean_comercios(_df(_comercio(), _comercio(id_comercio="x")))
        assert len(r.clean) == 1
        assert len(r.rejected) == 1


# ── clean_sucursales ──────────────────────────────────────────────────────────


class TestCleanSucursales:
    def test_happy_path(self):
        r = clean_sucursales(_df(_sucursal()))
        assert len(r.clean) == 1
        row = r.clean[0]
        assert row.sucursales_latitud == Decimal("-34.603722")
        assert row.sucursales_longitud == Decimal("-58.381592")
        assert row.sucursales_provincia == "AR-C"

    def test_blank_coordinates_become_none(self):
        r = clean_sucursales(_df(_sucursal(sucursales_latitud="", sucursales_longitud="")))
        assert len(r.clean) == 1
        assert r.clean[0].sucursales_latitud is None
        assert r.clean[0].sucursales_longitud is None

    def test_lat_out_of_range_rejected(self):
        r = clean_sucursales(_df(_sucursal(sucursales_latitud="10.0")))
        assert len(r.rejected) == 1
        assert "sucursales_latitud" in r.rejected[0]["reason"]

    def test_lon_out_of_range_rejected(self):
        r = clean_sucursales(_df(_sucursal(sucursales_longitud="-20.0")))
        assert len(r.rejected) == 1
        assert "sucursales_longitud" in r.rejected[0]["reason"]

    def test_invalid_provincia_rejected(self):
        r = clean_sucursales(_df(_sucursal(sucursales_provincia="AR-ZZ")))
        assert len(r.rejected) == 1
        assert "sucursales_provincia" in r.rejected[0]["reason"]

    @pytest.mark.parametrize("provincia", sorted(QUALITY_PROVINCIAS))
    def test_every_argentine_provincia_accepted(self, provincia):
        r = clean_sucursales(_df(_sucursal(sucursales_provincia=provincia)))
        assert len(r.clean) == 1

    def test_missing_provincia_rejected(self):
        r = clean_sucursales(_df(_sucursal(sucursales_provincia="")))
        assert len(r.rejected) == 1

    def test_optional_fields_are_none(self):
        r = clean_sucursales(_df(_sucursal(sucursales_barrio="", sucursales_observaciones="")))
        assert r.clean[0].sucursales_barrio is None
        assert r.clean[0].sucursales_observaciones is None


# ── clean_productos ───────────────────────────────────────────────────────────


class TestCleanProductos:
    def test_happy_path_no_promo(self):
        r = clean_productos(_df(_producto()))
        assert isinstance(r.clean, pd.DataFrame)
        assert len(r.clean) == 1
        row = r.clean.iloc[0]
        assert row["productos_precio_lista"] == "250.50"
        assert row["productos_precio_referencia"] == "240.00"
        assert bool(row["es_ean"])
        assert not bool(row["tiene_promo"])

    def test_ean_zero_parsed(self):
        r = clean_productos(_df(_producto(productos_ean="0")))
        assert not bool(r.clean.iloc[0]["es_ean"])

    def test_invalid_ean_rejected(self):
        r = clean_productos(_df(_producto(productos_ean="2")))
        assert len(r.rejected) == 1
        assert "productos_ean" in r.rejected[0]["reason"]
        assert r.rejected[0]["code"] == "productos_ean_invalid"

    def test_tiene_promo_true_when_promo1_set(self):
        r = clean_productos(_df(_producto(productos_precio_unitario_promo1="200.00")))
        assert bool(r.clean.iloc[0]["tiene_promo"])
        assert r.clean.iloc[0]["productos_precio_unitario_promo1"] == "200.00"

    def test_tiene_promo_true_when_promo2_set(self):
        r = clean_productos(_df(_producto(productos_precio_unitario_promo2="180.00")))
        assert bool(r.clean.iloc[0]["tiene_promo"])

    def test_tiene_promo_true_when_both_set(self):
        r = clean_productos(_df(_producto(
            productos_precio_unitario_promo1="200.00",
            productos_precio_unitario_promo2="180.00",
        )))
        assert bool(r.clean.iloc[0]["tiene_promo"])

    def test_tiene_promo_false_when_promotion_is_not_a_discount(self):
        r = clean_productos(_df(_producto(
            productos_precio_unitario_promo1="0",
            productos_precio_unitario_promo2="300.00",
        )))
        assert not bool(r.clean.iloc[0]["tiene_promo"])

    def test_tiene_promo_true_when_one_of_two_promotions_is_a_discount(self):
        r = clean_productos(_df(_producto(
            productos_precio_unitario_promo1="300.00",
            productos_precio_unitario_promo2="180.00",
        )))
        assert bool(r.clean.iloc[0]["tiene_promo"])

    def test_zero_precio_lista_rejected(self):
        r = clean_productos(_df(_producto(productos_precio_lista="0")))
        assert len(r.rejected) == 1
        assert r.rejected[0]["code"] == "productos_precio_lista_not_positive"

    def test_negative_precio_referencia_rejected(self):
        r = clean_productos(_df(_producto(productos_precio_referencia="-5.00")))
        assert len(r.rejected) == 1

    def test_non_numeric_price_rejected(self):
        r = clean_productos(_df(_producto(productos_precio_lista="N/A")))
        assert len(r.rejected) == 1
        assert "productos_precio_lista" in r.rejected[0]["reason"]
        assert r.rejected[0]["code"] == "productos_precio_lista_not_decimal"

    def test_blank_promo_becomes_none(self):
        r = clean_productos(_df(_producto(productos_precio_unitario_promo1="")))
        assert r.clean.iloc[0]["productos_precio_unitario_promo1"] is None

    def test_promo_leyenda_optional(self):
        r = clean_productos(_df(_producto(productos_leyenda_promo1="")))
        assert r.clean.iloc[0]["productos_leyenda_promo1"] is None


def _text(value):
    return "" if value is None else str(value)


@pytest.mark.parametrize(
    "lista, promo1, promo2, discount",
    [
        pytest.param(lista, promo1, promo2, discount, id=case_id)
        for case_id, lista, promo1, promo2, discount, _ in PRICE_CASES
        if lista is not None and lista > 0
    ],
)
def test_tiene_promo_matches_the_shared_discount_rule(lista, promo1, promo2, discount):
    r = clean_productos(_df(_producto(
        productos_precio_lista=_text(lista),
        productos_precio_unitario_promo1=_text(promo1),
        productos_precio_unitario_promo2=_text(promo2),
    )))
    assert bool(r.clean.iloc[0]["tiene_promo"]) == (discount is not None)
