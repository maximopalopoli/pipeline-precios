"""Rules for deciding when products can share an identity."""

from __future__ import annotations

from etl.transform.cleaner import ProductoRow


def is_valid_ean(value: str) -> bool:
    """Return whether *value* is a valid EAN-8 or EAN-13 code."""
    code = value.strip()
    if len(code) not in (8, 13) or not code.isascii() or not code.isdigit():
        return False

    checksum = sum(
        int(digit) * (3 if position % 2 else 1)
        for position, digit in enumerate(reversed(code[:-1]), start=1)
    )
    return (10 - checksum % 10) % 10 == int(code[-1])


def product_identity(row: ProductoRow) -> str:
    """Build a stable identity, global only for source-flagged valid EANs."""
    return product_identity_for_values(
        row.id_producto, row.es_ean, row.id_comercio, row.id_bandera
    )


def product_identity_for_values(
    id_producto: str,
    es_ean: bool,
    id_comercio: int,
    id_bandera: int,
) -> str:
    """Build a stable identity from the product fields used by bulk loaders."""
    if es_ean and is_valid_ean(id_producto):
        return f"ean:{id_producto}"
    return f"chain:{id_comercio}:{id_bandera}:{id_producto}"
