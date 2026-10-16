"""Names shoppers know chains by.

SEPA names a banner with its company's legal name or in capitals ("COTO
CICSA", "Cooperativa Obrera Limitada de Consumo y Vivienda"), and some
banners only by format ("Express", "Market").  Banners missing here keep
their published name.
"""

CHAIN_NAMES = {
    (3, 1): "Deheza",
    (4, 1): "Estación Lima",
    (5, 1): "California",
    (6, 1): "Comodín",
    (10, 1): "Carrefour Hiper",
    (10, 2): "Carrefour Market",
    (10, 3): "Carrefour Express",
    (10, 4): "Carrefour Maxi",
    (12, 1): "Coto",
    (13, 1): "La Coope",
    (15, 1): "Día",
    (20, 1): "La Agrícola Regional",
    (24, 1): "Simplicity",
    (24, 2): "Farmacity",
    (47, 1): "Unicoop",
}


def chain_name(id_comercio: int, id_bandera: int, published: str | None) -> str:
    """The banner's known name, or its published one."""
    return CHAIN_NAMES.get((id_comercio, id_bandera)) or published or f"Cadena {id_comercio}-{id_bandera}"
