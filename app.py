"""
Patient Cross-Checking System — single-file Streamlit app.
No external package dependencies beyond streamlit, pandas, and openpyxl.

Improvements applied (v2):
  Logic   1. Bidirectional cross-check: clinic→pharmacy pass added
          2. Configurable date-tolerance window (sidebar slider, default 7 days)
          3. Fuzzy name matching via difflib.SequenceMatcher (ratio threshold 0.80)
          4. Blank patient_id rows caught early → missing_id anomaly category
          5. Duplicate-dispensing detection (same drug, same patient, within window)
  Bugs    6. Clinic dates pre-computed on index build — not inside the inner loop
          7. infer_datetime_format replaced with format='mixed' (pandas 2.x compat)
          8. Results persisted in st.session_state — survive re-interactions
          9. applymap → map (pandas 2.1 compat)
  UX     10. Data preview (first 5 rows) in expander after each upload
         11. Optional fields (drug, quantity, diagnosis) shown in collapsible mapping section
         12. Severity + source filter on anomaly table
         13. Full report export: all pharmacy rows with anomaly_status column
         14. load_file moved to module scope (safe @st.cache_data behaviour)
"""
from __future__ import annotations

import csv
import difflib
import io
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
import streamlit as st

# ──────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────
REQUIRED_FIELDS           = ["patient_id", "patient_name", "visit_date"]
OPTIONAL_FIELDS           = ["drug_name", "quantity", "diagnosis"]
NAME_SIMILARITY_THRESHOLD = 0.80

_FIELD_HINTS: dict[str, list[str]] = {
    "patient_id":   ["patient_id", "patientid", "pat_id", "id", "mrn", "member_id"],
    "patient_name": ["patient_name", "patientname", "name", "full_name", "patient"],
    "visit_date":   ["visit_date", "visitdate", "date", "encounter_date", "appt_date", "dispensed_date"],
    "drug_name":    ["drug_name", "drug", "medication", "medicine", "product", "item"],
    "quantity":     ["quantity", "qty", "amount", "dispensed_qty", "units"],
    "diagnosis":    ["diagnosis", "diagnoses", "dx", "condition", "icd_code"],
}

_SEVERITY_ORDER = {"Critical": 0, "High": 1, "Medium": 2}


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
        for f, hints in _FIELD_HINTS.items():
            for col, norm in normalised.items():
                if any(h in norm for h in hints):
                    detected[f] = col
                    break
        return detected

    def validate_mapping(self, mapping: dict[str, str]) -> bool:
        return all(mapping.get(f) for f in REQUIRED_FIELDS)

    def remap_row(self, row: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any]:
        """Return a new dict with standardised field names, carrying extra columns through."""
        remapped = {std: row.get(src, "") for std, src in mapping.items() if src}
        mapped_sources = set(mapping.values())
        for k, v in row.items():
            if k not in mapped_sources:
                remapped[k] = v
        return remapped


# ──────────────────────────────────────────────
# Analysis helpers
# ──────────────────────────────────────────────
@dataclass
class AnalysisResult:
    summary_rows: list[dict[str, Any]] = field(default_factory=list)
    full_report:  list[dict[str, Any]] = field(default_factory=list)   # FIX 13
    stats:        dict[str, int]       = field(default_factory=dict)


def _normalise_date(raw: str) -> str:
    """Best-effort date normalisation to YYYY-MM-DD.
    FIX 7: format='mixed' replaces removed infer_datetime_format (pandas 2.x).
    """
    raw = raw.strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%Y%m%d"):
        try:
            return pd.to_datetime(raw, format=fmt).strftime("%Y-%m-%d")
        except Exception:
            pass
    try:
        return pd.to_datetime(raw, format="mixed", dayfirst=True).strftime("%Y-%m-%d")
    except Exception:
        return raw


def _name_similar(a: str, b: str, threshold: float) -> bool:
    """FIX 3: fuzzy name match — also handles transposed name order."""
    if not a or not b:
        return True
    direct = difflib.SequenceMatcher(None, a, b).ratio()
    sorted_ratio = difflib.SequenceMatcher(
        None,
        " ".join(sorted(a.split())),
        " ".join(sorted(b.split())),
    ).ratio()
    return max(direct, sorted_ratio) >= threshold


def _severity(anomaly_types: list[str]) -> str:
    if any(t in anomaly_types for t in (
        "missing_clinic_visit", "date_mismatch", "missing_pharmacy_pickup"
    )):
        return "Critical"
    if any(t in anomaly_types for t in ("name_mismatch", "duplicate_dispensing", "missing_id")):
        return "High"
    return "Medium"


# ──────────────────────────────────────────────
# Analysis Service
# ──────────────────────────────────────────────
class AnalysisService:
    """Cross-checks pharmacy records against clinic visit records."""

    def __init__(self) -> None:
        self._mapper = ColumnMapper()

    def run(
        self,
        pharmacy_rows:  list[dict],
        clinic_rows:    list[dict],
        pharmacy_map:   dict[str, str],
        clinic_map:     dict[str, str],
        date_tolerance: int   = 7,     # FIX 2
        name_threshold: float = NAME_SIMILARITY_THRESHOLD,  # FIX 3
    ) -> AnalysisResult:

        pharmacy = [self._mapper.remap_row(r, pharmacy_map) for r in pharmacy_rows]
        clinic   = [self._mapper.remap_row(r, clinic_map)   for r in clinic_rows]

        def _pid(rec: dict) -> str:
            return str(rec.get("patient_id", "")).strip().lower()

        # ── FIX 4: isolate blank-ID rows before any cross-check ───────────
        missing_id_anomalies: list[dict[str, Any]] = []
        clean_pharmacy: list[dict] = []
        clean_clinic:   list[dict] = []

        for rec in pharmacy:
            if not _pid(rec):
                missing_id_anomalies.append({
                    "patient_id":    "", "patient_name": rec.get("patient_name", ""),
                    "pharmacy_date": rec.get("visit_date", ""), "drug": rec.get("drug_name", ""),
                    "anomaly_types": "missing_id", "severity": "High",
                    "details": "Row has no patient_id — cannot cross-check.", "source": "pharmacy",
                })
            else:
                clean_pharmacy.append(rec)

        for rec in clinic:
            if not _pid(rec):
                missing_id_anomalies.append({
                    "patient_id":    "", "patient_name": rec.get("patient_name", ""),
                    "pharmacy_date": rec.get("visit_date", ""), "drug": "",
                    "anomaly_types": "missing_id", "severity": "High",
                    "details": "Clinic row has no patient_id — cannot cross-check.", "source": "clinic",
                })
            else:
                clean_clinic.append(rec)

        # ── FIX 6: pre-compute normalised timestamps on index build ────────
        def _stamp(rec: dict) -> pd.Timestamp | None:
            nd = _normalise_date(str(rec.get("visit_date", "")))
            try:
                return pd.to_datetime(nd)
            except Exception:
                return None

        clinic_index: dict[str, list[dict]] = {}
        for rec in clean_clinic:
            rec["_dt"] = _stamp(rec)
            clinic_index.setdefault(_pid(rec), []).append(rec)

        pharmacy_index: dict[str, list[dict]] = {}
        for rec in clean_pharmacy:
            rec["_dt"] = _stamp(rec)
            pharmacy_index.setdefault(_pid(rec), []).append(rec)

        anomalies: list[dict[str, Any]] = []

        # ── PASS 1: pharmacy → clinic ──────────────────────────────────────
        for prec in clean_pharmacy:
            pid    = _pid(prec)
            p_name = str(prec.get("patient_name", "")).strip().lower()
            p_dt   = prec["_dt"]
            p_date = str(prec.get("visit_date", ""))

            c_records     = clinic_index.get(pid, [])
            anomaly_types: list[str] = []
            details:       list[str] = []

            if not c_records:
                anomaly_types.append("missing_clinic_visit")
                details.append("No clinic visit found for this patient ID.")
            else:
                # FIX 3: fuzzy name match
                c_names = {str(r.get("patient_name", "")).strip().lower() for r in c_records}
                if p_name and not any(_name_similar(p_name, cn, name_threshold) for cn in c_names):
                    anomaly_types.append("name_mismatch")
                    details.append(
                        f"Pharmacy name '{prec.get('patient_name')}' doesn't match "
                        f"clinic '{', '.join(c_names)}' "
                        f"(similarity < {name_threshold:.0%})."
                    )

                # FIX 2 + FIX 6: configurable window, pre-computed dates
                if p_dt is not None:
                    c_dts = [r["_dt"] for r in c_records if r["_dt"] is not None]
                    if c_dts and not any(abs((p_dt - cd).days) <= date_tolerance for cd in c_dts):
                        anomaly_types.append("date_mismatch")
                        details.append(
                            f"Pharmacy date {p_date} has no clinic visit "
                            f"within {date_tolerance} days."
                        )

            # FIX 5: duplicate dispensing
            drug = str(prec.get("drug_name", "")).strip().lower()
            if drug:
                for other in pharmacy_index.get(pid, []):
                    if other is prec:
                        continue
                    if str(other.get("drug_name", "")).strip().lower() != drug:
                        continue
                    o_dt = other["_dt"]
                    if p_dt is not None and o_dt is not None:
                        gap = abs((p_dt - o_dt).days)
                        if 0 < gap <= date_tolerance:
                            anomaly_types.append("duplicate_dispensing")
                            details.append(
                                f"'{prec.get('drug_name')}' dispensed again "
                                f"{gap} day(s) apart for the same patient."
                            )
                            break

            if anomaly_types:
                deduped = list(dict.fromkeys(anomaly_types))
                anomalies.append({
                    "patient_id":    prec.get("patient_id", ""),
                    "patient_name":  prec.get("patient_name", ""),
                    "pharmacy_date": p_date,
                    "drug":          prec.get("drug_name", ""),
                    "anomaly_types": ", ".join(deduped),
                    "severity":      _severity(deduped),
                    "details":       " | ".join(details),
                    "source":        "pharmacy",
                })

        # ── PASS 2 (FIX 1): clinic → pharmacy reverse check ───────────────
        for crec in clean_clinic:
            pid    = _pid(crec)
            c_dt   = crec["_dt"]
            c_date = str(crec.get("visit_date", ""))

            p_records     = pharmacy_index.get(pid, [])
            anomaly_types = []
            details       = []

            if not p_records:
                anomaly_types.append("missing_pharmacy_pickup")
                details.append("Patient attended clinic but has no pharmacy dispensing record.")
            else:
                if c_dt is not None:
                    p_dts = [r["_dt"] for r in p_records if r["_dt"] is not None]
                    if p_dts and not any(abs((c_dt - pd_).days) <= date_tolerance for pd_ in p_dts):
                        anomaly_types.append("date_mismatch")
                        details.append(
                            f"Clinic visit {c_date} has no pharmacy dispensing "
                            f"within {date_tolerance} days."
                        )

            if anomaly_types:
                anomalies.append({
                    "patient_id":    crec.get("patient_id", ""),
                    "patient_name":  crec.get("patient_name", ""),
                    "pharmacy_date": "",
                    "drug":          "",
                    "anomaly_types": ", ".join(anomaly_types),
                    "severity":      _severity(anomaly_types),
                    "details":       " | ".join(details),
                    "source":        "clinic",
                })

        all_anomalies = missing_id_anomalies + anomalies
        all_anomalies.sort(key=lambda r: _SEVERITY_ORDER.get(r.get("severity", "Medium"), 2))

        # ── FIX 13: full report — pharmacy rows + anomaly_status ──────────
        anomaly_lookup: dict[tuple, str] = {
            (
                str(a.get("patient_id", "")).strip().lower(),
                str(a.get("pharmacy_date", "")).strip(),
            ): a.get("anomaly_types", "")
            for a in all_anomalies
            if a.get("source") == "pharmacy"
        }

        full_report: list[dict[str, Any]] = []
        for prec in clean_pharmacy:
            key    = (_pid(prec), str(prec.get("visit_date", "")).strip())
            row    = {k: v for k, v in prec.items() if not k.startswith("_")}
            row["anomaly_status"] = anomaly_lookup.get(key, "OK")
            full_report.append(row)

        sev_counts = {"Critical": 0, "High": 0, "Medium": 0}
        for a in all_anomalies:
            sev_counts[a.get("severity", "Medium")] = sev_counts.get(a.get("severity", "Medium"), 0) + 1

        return AnalysisResult(
            summary_rows=all_anomalies,
            full_report=full_report,
            stats={
                "total_analyzed":          len(pharmacy_rows),
                "total_anomalies":         len(all_anomalies),
                "severity_critical":       sev_counts["Critical"],
                "severity_high":           sev_counts["High"],
                "missing_pharmacy_pickup": sum(
                    1 for a in all_anomalies
                    if "missing_pharmacy_pickup" in a.get("anomaly_types", "")
                ),
            },
        )


# ──────────────────────────────────────────────
# Streamlit UI
# ──────────────────────────────────────────────
st.set_page_config(page_title="Patient Cross-Checking System", layout="wide")
st.title("Patient Cross-Checking System")
st.caption("Pharmacy & clinic visit validation for anomaly detection")


# FIX 14: module-scope definition ensures stable cache identity
@st.cache_data(show_spinner="Reading file…")
def load_file(name: str, content: bytes) -> list[dict[str, Any]]:
    return FileHandler().read_uploaded_file(name, content)


@st.cache_resource
def get_services() -> tuple[ColumnMapper, AnalysisService]:
    return ColumnMapper(), AnalysisService()


mapper, service = get_services()

# ── Sidebar ────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("Inputs")
    pharmacy_file = st.file_uploader("Pharmacy file", type=["csv", "xlsx", "xls"])
    clinic_file   = st.file_uploader("Clinic file",   type=["csv", "xlsx", "xls"])

    st.divider()
    st.header("Analysis settings")
    date_tolerance = st.slider(                        # FIX 2
        "Date tolerance (days)", min_value=1, max_value=90, value=7, step=1,
        help="Max days between a pharmacy dispensing and a clinic visit to be considered matching.",
    )
    name_threshold = st.slider(                        # FIX 3
        "Name similarity threshold", min_value=0.50, max_value=1.00,
        value=NAME_SIMILARITY_THRESHOLD, step=0.05, format="%.2f",
        help="Minimum similarity ratio for patient names to be considered matching (0 = anything, 1 = exact).",
    )


# ── Column mapping UI ──────────────────────────────────────────────────────
def mapping_ui(prefix: str, columns: list[str], detected: dict[str, str]) -> dict[str, str]:
    result: dict[str, str] = {}

    st.subheader(f"{prefix} — required fields")
    req_cols = st.columns(len(REQUIRED_FIELDS))
    for idx, f in enumerate(REQUIRED_FIELDS):
        preferred = detected.get(f)
        options   = [""] + columns
        index     = options.index(preferred) if preferred in options else 0
        with req_cols[idx]:
            result[f] = st.selectbox(f"`{f}` *", options, index=index, key=f"{prefix}_{f}")

    # FIX 11: optional fields in a collapsible section
    with st.expander(f"{prefix} — optional fields (drug, quantity, diagnosis)"):
        opt_cols = st.columns(len(OPTIONAL_FIELDS))
        for idx, f in enumerate(OPTIONAL_FIELDS):
            preferred = detected.get(f)
            options   = [""] + columns
            index     = options.index(preferred) if preferred in options else 0
            with opt_cols[idx]:
                result[f] = st.selectbox(f"`{f}`", options, index=index, key=f"{prefix}_{f}_opt")

    return result


# ── Main content ───────────────────────────────────────────────────────────
if pharmacy_file and clinic_file:
    try:
        pharmacy_rows = load_file(pharmacy_file.name, pharmacy_file.getvalue())
        clinic_rows   = load_file(clinic_file.name,   clinic_file.getvalue())
    except FileValidationError as exc:
        st.error(str(exc))
        st.stop()

    p_columns = list(pharmacy_rows[0].keys()) if pharmacy_rows else []
    c_columns = list(clinic_rows[0].keys())   if clinic_rows   else []

    st.success("Files loaded successfully")
    m1, m2 = st.columns(2)
    m1.metric("Pharmacy rows", len(pharmacy_rows))
    m2.metric("Clinic rows",   len(clinic_rows))

    # FIX 10: data preview expanders
    with st.expander("Preview pharmacy data (first 5 rows)"):
        st.dataframe(pd.DataFrame(pharmacy_rows[:5]), use_container_width=True)
    with st.expander("Preview clinic data (first 5 rows)"):
        st.dataframe(pd.DataFrame(clinic_rows[:5]),   use_container_width=True)

    pharmacy_map = mapping_ui("Pharmacy", p_columns, mapper.auto_detect_columns(p_columns))
    clinic_map   = mapping_ui("Clinic",   c_columns, mapper.auto_detect_columns(c_columns))

    if st.button("Run analysis", type="primary"):
        if not mapper.validate_mapping(pharmacy_map) or not mapper.validate_mapping(clinic_map):
            st.error("Please map patient_id, patient_name, and visit_date for both files.")
            st.stop()
        with st.spinner("Analysing records…"):
            # FIX 8: store in session_state so results survive reruns
            st.session_state["result"] = service.run(
                pharmacy_rows, clinic_rows,
                pharmacy_map,  clinic_map,
                date_tolerance=date_tolerance,
                name_threshold=name_threshold,
            )

    # FIX 8: render from session_state — persists across widget interactions
    result: AnalysisResult | None = st.session_state.get("result")
    if result is not None:
        st.subheader("Summary")
        s1, s2, s3, s4, s5 = st.columns(5)
        s1.metric("Analysed",        result.stats["total_analyzed"])
        s2.metric("Anomalies",       result.stats["total_anomalies"])
        s3.metric("Critical",        result.stats["severity_critical"])
        s4.metric("High",            result.stats["severity_high"])
        s5.metric("Missing pickups", result.stats["missing_pharmacy_pickup"])

        st.subheader("Anomalies")
        if result.summary_rows:
            df_out = pd.DataFrame(result.summary_rows)

            # FIX 12: severity + source filter
            fc1, fc2 = st.columns(2)
            with fc1:
                sev_filter = st.multiselect(
                    "Filter by severity",
                    options=["Critical", "High", "Medium"],
                    default=["Critical", "High", "Medium"],
                )
            with fc2:
                src_filter = st.multiselect(
                    "Filter by source",
                    options=["pharmacy", "clinic"],
                    default=["pharmacy", "clinic"],
                )

            df_filtered = df_out[
                df_out["severity"].isin(sev_filter) &
                df_out["source"].isin(src_filter)
            ]
            st.caption(f"Showing {len(df_filtered)} of {len(df_out)} anomalies")

            # FIX 9: .map replaces deprecated .applymap
            def _colour(val: str) -> str:
                return {
                    "Critical": "background-color: #ffd6d6",
                    "High":     "background-color: #fff3cd",
                    "Medium":   "background-color: #d6f0ff",
                }.get(val, "")

            st.dataframe(
                df_filtered.style.map(_colour, subset=["severity"]),
                use_container_width=True,
            )

            # Anomalies-only CSV
            buf_anomalies = io.StringIO()
            csv.DictWriter(buf_anomalies, fieldnames=list(result.summary_rows[0].keys())) \
               .writeheader() or None
            # Rebuild writer for full write
            buf_anomalies = io.StringIO()
            w = csv.DictWriter(buf_anomalies, fieldnames=list(result.summary_rows[0].keys()))
            w.writeheader()
            w.writerows(result.summary_rows)

            # FIX 13: full-report CSV
            buf_full = io.StringIO()
            if result.full_report:
                wf = csv.DictWriter(buf_full, fieldnames=list(result.full_report[0].keys()))
                wf.writeheader()
                wf.writerows(result.full_report)

            dl1, dl2 = st.columns(2)
            with dl1:
                st.download_button(
                    "⬇ Download anomalies (CSV)",
                    buf_anomalies.getvalue().encode("utf-8"),
                    file_name="anomalies_report.csv",
                    mime="text/csv",
                )
            with dl2:
                st.download_button(
                    "⬇ Download full report (CSV)",
                    buf_full.getvalue().encode("utf-8"),
                    file_name="full_report.csv",
                    mime="text/csv",
                )
        else:
            st.success("✅ No anomalies found for the selected files and mapping.")

else:
    st.info("Upload both pharmacy and clinic files to begin analysis.")
    st.session_state.pop("result", None)   # clear stale results when files removed

st.divider()
st.caption(
    "Requirements: streamlit · pandas · openpyxl  •  "
    "Deploy: set Streamlit entry-point to app.py"
)
