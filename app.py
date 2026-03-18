"""
Patient Cross-Checking System — single-file Streamlit app.
No external package dependencies beyond streamlit, pandas, and openpyxl.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
import streamlit as st

# ──────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────
REQUIRED_FIELDS = ["patient_id", "patient_name", "visit_date"]

# Candidate column name patterns for auto-detection
_FIELD_HINTS: dict[str, list[str]] = {
    "patient_id":   ["patient_id", "patientid", "pat_id", "id", "mrn", "member_id"],
    "patient_name": ["patient_name", "patientname", "name", "full_name", "patient"],
    "visit_date":   ["visit_date", "visitdate", "date", "encounter_date", "appt_date", "dispensed_date"],
    "drug_name":    ["drug_name", "drug", "medication", "medicine", "product", "item"],
    "quantity":     ["quantity", "qty", "amount", "dispensed_qty", "units"],
    "diagnosis":    ["diagnosis", "diagnoses", "dx", "condition", "icd_code"],
}


# ──────────────────────────────────────────────
# File Handler
# ──────────────────────────────────────────────
class FileValidationError(Exception):
    pass


class FileHandler:
    """Reads CSV / Excel uploads into a list of plain dicts."""

    def read_uploaded_file(self, filename: str, content: bytes) -> list[dict[str, Any]]:
        ext = filename.rsplit(".", 1)[-1].lower()
        try:
            if ext == "csv":
                df = pd.read_csv(io.BytesIO(content), dtype=str)
            elif ext in ("xlsx", "xls"):
                df = pd.read_excel(io.BytesIO(content), dtype=str)
            else:
                raise FileValidationError(f"Unsupported file type: .{ext}")
        except FileValidationError:
            raise
        except Exception as exc:
            raise FileValidationError(f"Could not parse '{filename}': {exc}") from exc

        df.columns = [str(c).strip() for c in df.columns]
        df = df.dropna(how="all").fillna("")
        if df.empty:
            raise FileValidationError(f"'{filename}' appears to be empty.")
        return df.to_dict(orient="records")


# ──────────────────────────────────────────────
# Column Mapper
# ──────────────────────────────────────────────
class ColumnMapper:
    """Auto-detects and validates column mappings."""

    def auto_detect_columns(self, columns: list[str]) -> dict[str, str]:
        normalised = {c: c.lower().replace(" ", "_").replace("-", "_") for c in columns}
        detected: dict[str, str] = {}
        for field, hints in _FIELD_HINTS.items():
            for col, norm in normalised.items():
                if any(h in norm for h in hints):
                    detected[field] = col
                    break
        return detected

    def validate_mapping(self, mapping: dict[str, str]) -> bool:
        return all(mapping.get(f) for f in REQUIRED_FIELDS)

    def remap_row(self, row: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any]:
        """Return a new dict with standardised field names."""
        remapped = {std: row.get(src, "") for std, src in mapping.items() if src}
        # Carry through any extra columns not in the mapping
        mapped_sources = set(mapping.values())
        for k, v in row.items():
            if k not in mapped_sources:
                remapped[k] = v
        return remapped


# ──────────────────────────────────────────────
# Analysis Service
# ──────────────────────────────────────────────
@dataclass
class AnalysisResult:
    summary_rows: list[dict[str, Any]] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)


def _normalise_date(raw: str) -> str:
    """Best-effort date normalisation to YYYY-MM-DD."""
    raw = raw.strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%Y%m%d"):
        try:
            return pd.to_datetime(raw, format=fmt).strftime("%Y-%m-%d")
        except Exception:
            pass
    try:
        return pd.to_datetime(raw, dayfirst=True, infer_datetime_format=True).strftime("%Y-%m-%d")
    except Exception:
        return raw


def _severity(anomaly_types: list[str]) -> str:
    if any(t in anomaly_types for t in ("missing_clinic_visit", "date_mismatch")):
        return "Critical"
    if "name_mismatch" in anomaly_types:
        return "High"
    return "Medium"


class AnalysisService:
    """Cross-checks pharmacy records against clinic visit records."""

    def __init__(self):
        self._mapper = ColumnMapper()

    def run(
        self,
        pharmacy_rows: list[dict],
        clinic_rows: list[dict],
        pharmacy_map: dict[str, str],
        clinic_map: dict[str, str],
    ) -> AnalysisResult:
        pharmacy = [self._mapper.remap_row(r, pharmacy_map) for r in pharmacy_rows]
        clinic   = [self._mapper.remap_row(r, clinic_map)   for r in clinic_rows]

        # Build clinic lookup: patient_id → list of records
        clinic_index: dict[str, list[dict]] = {}
        for rec in clinic:
            pid = str(rec.get("patient_id", "")).strip().lower()
            clinic_index.setdefault(pid, []).append(rec)

        anomalies: list[dict[str, Any]] = []
        total_analyzed = len(pharmacy)

        for prec in pharmacy:
            pid = str(prec.get("patient_id", "")).strip().lower()
            p_name = str(prec.get("patient_name", "")).strip().lower()
            p_date = _normalise_date(str(prec.get("visit_date", "")))

            c_records = clinic_index.get(pid, [])
            anomaly_types: list[str] = []
            details: list[str] = []

            if not c_records:
                anomaly_types.append("missing_clinic_visit")
                details.append("No clinic visit found for this patient ID.")
            else:
                # Check name consistency
                c_names = {str(r.get("patient_name", "")).strip().lower() for r in c_records}
                if p_name and not any(p_name in cn or cn in p_name for cn in c_names):
                    anomaly_types.append("name_mismatch")
                    details.append(
                        f"Name in pharmacy '{prec.get('patient_name')}' "
                        f"doesn't match clinic '{', '.join(c_names)}'."
                    )

                # Check date proximity (within 7 days)
                c_dates = []
                for r in c_records:
                    nd = _normalise_date(str(r.get("visit_date", "")))
                    try:
                        c_dates.append(pd.to_datetime(nd))
                    except Exception:
                        pass

                try:
                    p_dt = pd.to_datetime(p_date)
                    if c_dates and not any(abs((p_dt - cd).days) <= 7 for cd in c_dates):
                        anomaly_types.append("date_mismatch")
                        details.append(
                            f"Pharmacy visit date {p_date} has no clinic visit within 7 days."
                        )
                except Exception:
                    pass

            if anomaly_types:
                sev = _severity(anomaly_types)
                anomalies.append({
                    "patient_id":     prec.get("patient_id", ""),
                    "patient_name":   prec.get("patient_name", ""),
                    "pharmacy_date":  prec.get("visit_date", ""),
                    "drug":           prec.get("drug_name", ""),
                    "anomaly_types":  ", ".join(anomaly_types),
                    "severity":       sev,
                    "details":        " | ".join(details),
                })

        sev_counts = {"Critical": 0, "High": 0, "Medium": 0}
        for a in anomalies:
            sev_counts[a["severity"]] = sev_counts.get(a["severity"], 0) + 1

        return AnalysisResult(
            summary_rows=anomalies,
            stats={
                "total_analyzed":    total_analyzed,
                "total_anomalies":   len(anomalies),
                "severity_critical": sev_counts["Critical"],
                "severity_high":     sev_counts["High"],
            },
        )


# ──────────────────────────────────────────────
# Streamlit UI
# ──────────────────────────────────────────────
st.set_page_config(page_title="Patient Cross-Checking System", layout="wide")
st.title("Patient Cross-Checking System")
st.caption("Pharmacy & clinic visit validation for anomaly detection")

# Initialise services once (cached at module level, not inside loops)
@st.cache_resource
def get_services():
    return FileHandler(), ColumnMapper(), AnalysisService()

file_handler, mapper, service = get_services()

with st.sidebar:
    st.header("Inputs")
    pharmacy_file = st.file_uploader("Pharmacy file", type=["csv", "xlsx", "xls"])
    clinic_file   = st.file_uploader("Clinic file",   type=["csv", "xlsx", "xls"])


def mapping_ui(prefix: str, columns: list[str], detected: dict[str, str]) -> dict[str, str]:
    st.subheader(f"{prefix} column mapping")
    result: dict[str, str] = {}
    col_pairs = st.columns(len(REQUIRED_FIELDS))
    for idx, f in enumerate(REQUIRED_FIELDS):
        preferred = detected.get(f)
        options   = [""] + columns
        index     = options.index(preferred) if preferred in options else 0
        with col_pairs[idx]:
            result[f] = st.selectbox(f"`{f}`", options, index=index, key=f"{prefix}_{f}")
    return result


if pharmacy_file and clinic_file:
    # Cache parsed file content keyed by file name + size to avoid re-parsing on every interaction
    @st.cache_data(show_spinner="Reading files…")
    def load_file(name: str, content: bytes):
        return FileHandler().read_uploaded_file(name, content)

    try:
        pharmacy_rows = load_file(pharmacy_file.name, pharmacy_file.getvalue())
        clinic_rows   = load_file(clinic_file.name,   clinic_file.getvalue())
    except FileValidationError as exc:
        st.error(str(exc))
        st.stop()

    p_columns = list(pharmacy_rows[0].keys()) if pharmacy_rows else []
    c_columns = list(clinic_rows[0].keys())   if clinic_rows   else []

    st.success("Files loaded successfully")
    c1, c2 = st.columns(2)
    c1.metric("Pharmacy rows", len(pharmacy_rows))
    c2.metric("Clinic rows",   len(clinic_rows))

    pharmacy_map = mapping_ui("Pharmacy", p_columns, mapper.auto_detect_columns(p_columns))
    clinic_map   = mapping_ui("Clinic",   c_columns, mapper.auto_detect_columns(c_columns))

    if st.button("Run analysis", type="primary"):
        if not mapper.validate_mapping(pharmacy_map) or not mapper.validate_mapping(clinic_map):
            st.error("Please map patient_id, patient_name, and visit_date for both files.")
            st.stop()

        with st.spinner("Analysing records…"):
            result = service.run(pharmacy_rows, clinic_rows, pharmacy_map, clinic_map)

        st.subheader("Summary")
        s1, s2, s3, s4 = st.columns(4)
        s1.metric("Analysed",  result.stats["total_analyzed"])
        s2.metric("Anomalies", result.stats["total_anomalies"])
        s3.metric("Critical",  result.stats["severity_critical"])
        s4.metric("High",      result.stats["severity_high"])

        st.subheader("Anomalies")
        if result.summary_rows:
            df_out = pd.DataFrame(result.summary_rows)
            # Colour-code severity
            def _colour(val: str) -> str:
                return {
                    "Critical": "background-color: #ffd6d6",
                    "High":     "background-color: #fff3cd",
                    "Medium":   "background-color: #d6f0ff",
                }.get(val, "")

            st.dataframe(
                df_out.style.applymap(_colour, subset=["severity"]),
                use_container_width=True,
            )

            output = io.StringIO()
            writer = csv.DictWriter(output, fieldnames=list(result.summary_rows[0].keys()))
            writer.writeheader()
            writer.writerows(result.summary_rows)
            st.download_button(
                "⬇ Download anomalies (CSV)",
                output.getvalue().encode("utf-8"),
                file_name="anomalies_report.csv",
                mime="text/csv",
            )
        else:
            st.success("✅ No anomalies found for the selected files and mapping.")

else:
    st.info("Upload both pharmacy and clinic files to begin analysis.")

st.divider()
st.caption("Deploy tip: set Streamlit entry-point to app.py  •  Requirements: streamlit, pandas, openpyxl")
