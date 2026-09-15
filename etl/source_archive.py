"""Inspect and validate the business date declared by a SEPA archive."""

from __future__ import annotations

import re
import zipfile
from datetime import date
from pathlib import PurePosixPath


_DATE_RE = re.compile(r"(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)")


class SourceArchiveDateError(ValueError):
    """Raised when an archive does not declare one unambiguous source date."""


class SourceArchiveDateMismatchError(SourceArchiveDateError):
    """Raised when the archive date differs from the requested logical date."""


def inspect_source_archive_date(zip_path: str) -> date:
    """Return the one date declared by the nested SEPA archive names.

    A valid archive has every inner ZIP under a dated directory and includes
    the same ISO date in each inner ZIP filename.  Checking names only keeps
    this pre-staging validation cheap even for large product CSVs.
    """
    declared_dates: set[date] = set()

    with zipfile.ZipFile(zip_path) as archive:
        inner_names = [
            name for name in archive.namelist() if name.lower().endswith(".zip")
        ]

    if not inner_names:
        raise SourceArchiveDateError("SEPA archive contains no inner ZIP files.")

    for inner_name in inner_names:
        path = PurePosixPath(inner_name)
        name_dates = _dates_in_name(path.name)
        directory_dates = _dates_in_name("/".join(path.parts[:-1]))
        if len(name_dates) != 1 or len(directory_dates) != 1:
            raise SourceArchiveDateError(
                f"Cannot determine one source date from inner archive {inner_name!r}."
            )
        if name_dates != directory_dates:
            raise SourceArchiveDateError(
                f"Conflicting dates in inner archive {inner_name!r}."
            )
        declared_dates.update(name_dates)

    if len(declared_dates) != 1:
        values = ", ".join(sorted(value.isoformat() for value in declared_dates))
        raise SourceArchiveDateError(f"SEPA archive declares multiple dates: {values}.")
    return declared_dates.pop()


def validate_source_archive_date(*, fecha_logica: date, fecha_fuente: date) -> None:
    """Require the downloaded source snapshot to match its logical run date."""
    if fecha_fuente != fecha_logica:
        raise SourceArchiveDateMismatchError(
            "SEPA archive date "
            f"{fecha_fuente.isoformat()} does not match logical ingestion date "
            f"{fecha_logica.isoformat()}."
        )


def _dates_in_name(value: str) -> set[date]:
    dates: set[date] = set()
    for match in _DATE_RE.finditer(value):
        try:
            dates.add(date.fromisoformat(match.group(1)))
        except ValueError as exc:
            raise SourceArchiveDateError(
                f"Invalid ISO date {match.group(1)!r} in archive name {value!r}."
            ) from exc
    return dates
