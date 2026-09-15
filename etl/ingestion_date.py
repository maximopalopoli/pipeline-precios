"""Shared rules for determining the date represented by an ingestion."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo


ARGENTINA_TIMEZONE = ZoneInfo("America/Argentina/Buenos_Aires")


def resolve_ingestion_date(
    fecha: date | None = None,
    *,
    now: datetime | None = None,
) -> date:
    """Return the date an ingestion represents.

    An explicitly requested date is authoritative.  Without one, use the
    calendar day in Argentina rather than the host machine's local timezone.
    ``now`` exists to make the boundary deterministic in tests.
    """
    if fecha is not None:
        return fecha

    if now is None:
        return datetime.now(ARGENTINA_TIMEZONE).date()

    if now.tzinfo is None:
        raise ValueError("now must include a timezone")
    return now.astimezone(ARGENTINA_TIMEZONE).date()
