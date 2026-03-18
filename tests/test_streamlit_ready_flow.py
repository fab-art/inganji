from patient_crosscheck.analyzer import AnalysisService
from patient_crosscheck.file_handler import FileHandler


def test_read_uploaded_csv() -> None:
    payload = b"patient_rama_number,patient_name,dispensing_date\nR1,Alice,2026-03-01\n"
    rows = FileHandler().read_uploaded_file("pharmacy.csv", payload)
    assert len(rows) == 1
    assert rows[0]["patient_rama_number"] == "R1"


def test_analysis_service_unmatched_case() -> None:
    pharmacy_rows = [
        {"patient_rama_number": "R2", "patient_name": "Bob", "dispensing_date": "2026-03-15"}
    ]
    clinic_rows = [
        {"RAMA NUMBER": "R1", "BENEFICIARY'S NAMES": "Alice", "DATE": "2026-03-10"}
    ]
    result = AnalysisService().run(
        pharmacy_rows,
        clinic_rows,
        pharmacy_map={"patient_id": "patient_rama_number", "patient_name": "patient_name", "visit_date": "dispensing_date"},
        clinic_map={"patient_id": "RAMA NUMBER", "patient_name": "BENEFICIARY'S NAMES", "visit_date": "DATE"},
    )

    assert result.stats["total_analyzed"] == 1
    assert result.stats["total_anomalies"] >= 1
