from patient_crosscheck.column_mapper import ColumnMapper
from patient_crosscheck.patient_matcher import PatientMatcher


def test_column_mapper_auto_detects_expected_fields() -> None:
    columns = ["RAMA NUMBER", "BENEFICIARY'S NAMES", "DATE"]
    mapper = ColumnMapper()
    mapping = mapper.auto_detect_columns(columns)

    assert mapping["patient_id"] == "RAMA NUMBER"
    assert mapping["patient_name"] == "BENEFICIARY'S NAMES"
    assert mapping["visit_date"] == "DATE"


def test_patient_matcher_matches_by_rama_and_scores() -> None:
    pharmacy_rows = [
        {
            "patient_rama_number": "R1",
            "patient_name": "Alice Uwase",
            "dispensing_date": "2026-03-15",
        }
    ]
    clinic_rows = [
        {
            "RAMA NUMBER": "R1",
            "BENEFICIARY'S NAMES": "Alice Uwase",
            "DATE": "2026-03-10",
        }
    ]

    matcher = PatientMatcher()
    results = matcher.match_by_rama(
        pharmacy_rows,
        clinic_rows,
        pharmacy_map={"patient_id": "patient_rama_number", "patient_name": "patient_name", "visit_date": "dispensing_date"},
        clinic_map={"patient_id": "RAMA NUMBER", "patient_name": "BENEFICIARY'S NAMES", "visit_date": "DATE"},
    )

    assert len(results) == 1
    assert results[0].rama_match is True
    assert results[0].date_valid is True
    assert results[0].confidence >= 90
