from __future__ import annotations

import csv
import io
from pathlib import Path

import streamlit as st

from patient_crosscheck.analyzer import AnalysisService
from patient_crosscheck.column_mapper import ColumnMapper, REQUIRED_FIELDS
from patient_crosscheck.file_handler import FileHandler, FileValidationError

st.set_page_config(page_title="Patient Cross-Checking System", layout="wide")

st.title("Patient Cross-Checking System")
st.caption("Pharmacy & clinic visit validation for anomaly detection")

file_handler = FileHandler()
mapper = ColumnMapper()
service = AnalysisService()

with st.sidebar:
    st.header("Inputs")
    pharmacy_file = st.file_uploader("Pharmacy file", type=["csv", "xlsx", "xls"])
    clinic_file = st.file_uploader("Clinic file", type=["csv", "xlsx", "xls"])


def mapping_ui(prefix: str, columns: list[str], detected: dict[str, str]) -> dict[str, str]:
    st.subheader(f"{prefix} column mapping")
    result: dict[str, str] = {}
    for field in REQUIRED_FIELDS:
        preferred = detected.get(field)
        options = [""] + columns
        index = options.index(preferred) if preferred in options else 0
        choice = st.selectbox(f"{prefix} {field}", options, index=index, key=f"{prefix}_{field}")
        result[field] = choice
    return result


if pharmacy_file and clinic_file:
    try:
        pharmacy_rows = file_handler.read_uploaded_file(pharmacy_file.name, pharmacy_file.getvalue())
        clinic_rows = file_handler.read_uploaded_file(clinic_file.name, clinic_file.getvalue())
    except FileValidationError as exc:
        st.error(str(exc))
        st.stop()

    p_columns = list(pharmacy_rows[0].keys()) if pharmacy_rows else []
    c_columns = list(clinic_rows[0].keys()) if clinic_rows else []

    st.success("Files loaded successfully")
    c1, c2 = st.columns(2)
    c1.metric("Pharmacy rows", len(pharmacy_rows))
    c2.metric("Clinic rows", len(clinic_rows))

    pharmacy_map = mapping_ui("Pharmacy", p_columns, mapper.auto_detect_columns(p_columns))
    clinic_map = mapping_ui("Clinic", c_columns, mapper.auto_detect_columns(c_columns))

    if st.button("Run analysis", type="primary"):
        if not mapper.validate_mapping(pharmacy_map) or not mapper.validate_mapping(clinic_map):
            st.error("Please map patient_id, patient_name, and visit_date for both files.")
            st.stop()

        result = service.run(pharmacy_rows, clinic_rows, pharmacy_map, clinic_map)

        st.subheader("Summary")
        s1, s2, s3, s4 = st.columns(4)
        s1.metric("Analyzed", result.stats["total_analyzed"])
        s2.metric("Anomalies", result.stats["total_anomalies"])
        s3.metric("Critical", result.stats["severity_critical"])
        s4.metric("High", result.stats["severity_high"])

        st.subheader("Anomalies")
        if result.summary_rows:
            st.dataframe(result.summary_rows, use_container_width=True)

            output = io.StringIO()
            writer = csv.DictWriter(output, fieldnames=list(result.summary_rows[0].keys()))
            writer.writeheader()
            writer.writerows(result.summary_rows)
            st.download_button(
                "Download anomalies (CSV)",
                output.getvalue().encode("utf-8"),
                file_name="anomalies_report.csv",
                mime="text/csv",
            )
        else:
            st.info("No anomalies found for the selected files and mapping.")
else:
    st.info("Upload both pharmacy and clinic files to begin analysis.")

st.divider()
st.caption("Deploy tip: set Streamlit entrypoint to app.py")
