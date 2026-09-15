import zipfile
from datetime import date

import pytest

from etl.source_archive import (
    SourceArchiveDateError,
    SourceArchiveDateMismatchError,
    inspect_source_archive_date,
    validate_source_archive_date,
)


def _archive(tmp_path, *inner_names: str):
    path = tmp_path / "source.zip"
    with zipfile.ZipFile(path, "w") as archive:
        for name in inner_names:
            archive.writestr(name, b"inner zip placeholder")
    return path


def test_inspects_the_consistent_date_in_nested_archive_names(tmp_path):
    archive = _archive(
        tmp_path,
        "2026-03-17/sepa_1_comercio-sepa-8_2026-03-17_09-05-11.zip",
        "2026-03-17/sepa_1_comercio-sepa-15_2026-03-17_09-05-11.zip",
    )

    assert inspect_source_archive_date(str(archive)) == date(2026, 3, 17)


def test_rejects_conflicting_inner_archive_dates(tmp_path):
    archive = _archive(
        tmp_path,
        "2026-03-17/sepa_1_comercio-sepa-8_2026-03-17_09-05-11.zip",
        "2026-03-17/sepa_1_comercio-sepa-15_2026-03-18_09-05-11.zip",
    )

    with pytest.raises(SourceArchiveDateError, match="Conflicting dates"):
        inspect_source_archive_date(str(archive))


def test_rejects_archive_without_a_dated_directory(tmp_path):
    archive = _archive(tmp_path, "sepa_1_comercio-sepa-8_2026-03-17_09-05-11.zip")

    with pytest.raises(SourceArchiveDateError, match="Cannot determine"):
        inspect_source_archive_date(str(archive))


def test_rejects_archive_that_does_not_match_the_logical_date():
    with pytest.raises(SourceArchiveDateMismatchError, match="does not match"):
        validate_source_archive_date(
            fecha_logica=date(2026, 3, 17),
            fecha_fuente=date(2026, 3, 16),
        )
