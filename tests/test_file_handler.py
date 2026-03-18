from pathlib import Path

import pytest

from patient_crosscheck.file_handler import FileHandler, FileValidationError


def test_validate_file_rejects_missing_file(tmp_path: Path) -> None:
    fh = FileHandler()
    with pytest.raises(FileValidationError):
        fh.validate_file(tmp_path / "missing.csv")


def test_read_csv_and_metadata(tmp_path: Path) -> None:
    file_path = tmp_path / "pharmacy.csv"
    file_path.write_text(
        "patient_rama_number,patient_name,dispensing_date\n"
        "R1,Alice,2026-03-01\n"
        "R2,Bob,2026-03-05\n",
        encoding="utf-8",
    )

    fh = FileHandler()
    loaded = fh.read_file(file_path)
    meta = fh.get_file_metadata(file_path)

    assert len(loaded) == 2
    assert meta.row_count == 2
    assert meta.column_count == 3
    assert meta.min_date is not None
    assert meta.max_date is not None


def test_validate_file_size_limit(tmp_path: Path) -> None:
    file_path = tmp_path / "big.csv"
    file_path.write_text("x" * 200)

    fh = FileHandler()
    fh.config = fh.config.__class__(max_file_size_mb=0)

    with pytest.raises(FileValidationError):
        fh.validate_file(file_path)
