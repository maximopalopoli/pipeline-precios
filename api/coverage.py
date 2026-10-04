"""Why a product's prices compare poorly, as codes the web client explains.

- ``fraccionado_en_local``: a 13-digit code with GS1 prefix 2, the range
  for restricted circulation that stores use for what they weigh or cut
  themselves (cheese, cold cuts, meat, produce).  Each chain numbers those
  items its own way, so their prices do not compare across chains.  On the
  local AMBA day about 5,000 of the 6,800 products without a valid EAN have
  one, and some chains flag 2,000 more as EAN.
- ``codigo_interno``: any other product without a valid EAN; its code is the
  chain's own (see the product identity rule).
- ``pocos_locales``: fewer than ``FEW_STORES`` of the chosen stores carry it.
"""

from __future__ import annotations

import re

IN_STORE_CODE = re.compile(r"^2\d{12}$")
FEW_STORES = 3


def coverage_notes(es_ean: bool, id_producto: str | None, sucursales: int | None = None) -> list[str]:
    """Coverage codes of a product; ``sucursales`` counts the stores carrying it."""
    notes = []
    if IN_STORE_CODE.match(id_producto or ""):
        notes.append("fraccionado_en_local")
    elif not es_ean:
        notes.append("codigo_interno")
    if sucursales is not None and 0 < sucursales < FEW_STORES:
        notes.append("pocos_locales")
    return notes
