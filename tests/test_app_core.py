from app import AnalysisService, ColumnMapper, FileHandler, FileValidationError


def test_column_mapper_auto_detects_expected_fields() -> None:
    columns = ["RAMA NUMBER", "BENEFICIARY'S NAMES", "DATE"]
    mapping = ColumnMapper().auto_detect_columns(columns)
    assert mapping["patient_id"] == "RAMA NUMBER"
    assert mapping["patient_name"] == "BENEFICIARY'S NAMES"
    assert mapping["visit_date"] == "DATE"


def test_file_handler_reads_uploaded_csv_and_builds_metadata() -> None:
    payload = b"patient_rama_number,patient_name,dispensing_date\nR1,Alice,2026-03-01\n"
    handler = FileHandler()
    rows = handler.read_uploaded_file("pharmacy.csv", payload)
    metadata = handler.build_metadata("pharmacy.csv", rows, len(payload))

    assert len(rows) == 1
    assert metadata.row_count == 1
    assert metadata.column_count == 3
    assert metadata.min_date is not None


def test_file_handler_rejects_unsupported_extension() -> None:
    handler = FileHandler()
    try:
        handler.read_uploaded_file("notes.txt", b"hello")
    except FileValidationError:
        assert True
        return
    assert False, "Expected unsupported extension to raise FileValidationError"


def test_analysis_service_returns_unmatched_anomaly() -> None:
    pharmacy_rows = [{"patient_rama_number": "R2", "patient_name": "Bob", "dispensing_date": "2026-03-15"}]
    clinic_rows = [{"RAMA NUMBER": "R1", "BENEFICIARY'S NAMES": "Alice", "DATE": "2026-03-10"}]

    result = AnalysisService().run(
        pharmacy_rows,
        clinic_rows,
        pharmacy_map={"patient_id": "patient_rama_number", "patient_name": "patient_name", "visit_date": "dispensing_date"},
        clinic_map={"patient_id": "RAMA NUMBER", "patient_name": "BENEFICIARY'S NAMES", "visit_date": "DATE"},
    )

    assert result.stats["total_analyzed"] == 1
    assert result.stats["total_anomalies"] >= 1
    assert any(item.kind == "unmatched_pharmacy_visit" for item in result.anomalies)
