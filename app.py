"""
RSSB Medical Claim Fraud Detection System
Single-file Streamlit app.

Requirements:
    streamlit
    pandas
    openpyxl
    rapidfuzz
    networkx
    plotly

Fraud patterns detected:
  1. Ghost patients          — pharmacy RAMA has no matching clinic visit
  2. Dispense before visit   — drug dispensed before the consultation date
  3. Name discrepancy        — fuzzy name mismatch for same RAMA (data quality signal)
  4. Doctor–patient network  — visualise prescribing relationships for pattern detection

Pharmacy normalisation:
  Multiple pharmacy rows with the same RAMA + dispensing date are collapsed into
  one record (one visit) with all drugs concatenated before cross-checking.
"""
from __future__ import annotations

import io
from typing import Any

import networkx as nx
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from rapidfuzz import fuzz as rfuzz

# ──────────────────────────────────────────────
# CONFIGURATION
# ──────────────────────────────────────────────
SCORE_NO_CLINIC_VISIT       = 50
SCORE_NAME_MISMATCH         = 20
SCORE_DISPENSE_BEFORE_VISIT = 20

DEFAULT_NAME_THRESHOLD = 80   # 0–100 rapidfuzz score

RISK_COLORS = {"HIGH": "#FF4B4B", "MEDIUM": "#FFA600", "LOW": "#21C354"}

_SORT_ASCENDING = {
    "Fraud Score":     False,   # highest first — most suspicious
    "Date Diff (days)": True,   # most negative first — dispensed earliest before visit
    "Name Similarity": True,    # lowest similarity first — weakest matches
    "Visit Found":     True,    # NOT_FOUND alphabetically before FOUND
}

# Column auto-detection hints (all lowercase for normalisation)
_CLINIC_HINTS: dict[str, list[str]] = {
    "rama":       ["rama", "rssb", "member_no", "member", "beneficiary_id", "id", "number", "numero", "no"],
    "name":       ["patient_name", "name", "nom", "full_name", "beneficiary", "beneficiaire"],
    "visit_date": ["visit_date", "consultation_date", "date_visit", "date", "visite"],
    "doctor":     ["doctor", "medecin", "physician", "dr_name", "prescriber", "prescripteur"],
}

_PHARMACY_HINTS: dict[str, list[str]] = {
    "rama":            ["rama", "rssb", "member_no", "member", "beneficiary_id", "id", "number", "numero", "no"],
    "name":            ["patient_name", "name", "nom", "full_name", "beneficiary", "beneficiaire"],
    "dispensing_date": ["dispensing_date", "dispense_date", "date_dispensed", "date", "delivery_date"],
    "drug":            ["drug_name", "drug", "medication", "medicament", "medicine", "item", "product", "article", "denomination"],
    "pharmacy_name":   ["pharmacy_name", "pharmacy", "pharmacie", "provider", "facility"],
}


# ──────────────────────────────────────────────
# FILE LOADING  (module-scope → stable cache key)
# ──────────────────────────────────────────────
@st.cache_data(show_spinner="Reading file…")
def load_file(name: str, content: bytes) -> pd.DataFrame:
    ext = name.rsplit(".", 1)[-1].lower()
    try:
        if ext == "csv":
            df = pd.read_csv(io.BytesIO(content), dtype=str)
        elif ext in ("xlsx", "xls"):
            df = pd.read_excel(io.BytesIO(content), dtype=str)
        else:
            st.error(f"Unsupported file type: .{ext}")
            st.stop()
    except Exception as exc:
        st.error(f"Could not parse '{name}': {exc}")
        st.stop()
    df.columns = [str(c).strip() for c in df.columns]
    return df.fillna("")


# ──────────────────────────────────────────────
# COLUMN AUTO-DETECTION
# ──────────────────────────────────────────────
def auto_detect(columns: list[str], hints: dict[str, list[str]]) -> dict[str, str]:
    normalised = {c: c.lower().replace(" ", "_").replace("-", "_") for c in columns}
    detected: dict[str, str] = {}
    for field, h_list in hints.items():
        for col, norm in normalised.items():
            if any(h in norm for h in h_list):
                detected[field] = col
                break
    return detected


# ──────────────────────────────────────────────
# DATA CLEANING
# ──────────────────────────────────────────────
def _clean_str(series: pd.Series) -> pd.Series:
    return series.astype(str).str.strip().str.upper()


def clean_clinic(df: pd.DataFrame, mapping: dict[str, str]) -> pd.DataFrame:
    out = pd.DataFrame()
    out["rama"]       = _clean_str(df[mapping["rama"]])
    out["name_clean"] = _clean_str(df[mapping["name"]])
    out["visit_date"] = pd.to_datetime(
        df[mapping["visit_date"]], errors="coerce", dayfirst=True
    )
    out["doctor"] = (
        _clean_str(df[mapping["doctor"]])
        if mapping.get("doctor")
        else ""
    )
    return out.dropna(subset=["visit_date"]).reset_index(drop=True)


def clean_pharmacy(df: pd.DataFrame, mapping: dict[str, str]) -> pd.DataFrame:
    out = pd.DataFrame()
    out["rama"]            = _clean_str(df[mapping["rama"]])
    out["name_clean"]      = _clean_str(df[mapping["name"]])
    out["dispensing_date"] = pd.to_datetime(
        df[mapping["dispensing_date"]], errors="coerce", dayfirst=True
    )
    out["drug"] = (
        _clean_str(df[mapping["drug"]])
        if mapping.get("drug")
        else ""
    )
    out["pharmacy_name"] = (
        _clean_str(df[mapping["pharmacy_name"]])
        if mapping.get("pharmacy_name")
        else ""
    )
    return out.dropna(subset=["dispensing_date"]).reset_index(drop=True)


# ──────────────────────────────────────────────
# PHARMACY NORMALISATION
# One RAMA + dispensing_date = one visit.
# Multiple drug rows → concatenated drug list.
# ──────────────────────────────────────────────
def normalise_pharmacy(df: pd.DataFrame) -> pd.DataFrame:
    """
    Collapse multiple rows that share the same RAMA + dispensing_date
    (one pharmacy visit with multiple drug lines) into a single record.
    Drugs are deduplicated and joined with ' | '.
    """
    def _join_drugs(series: pd.Series) -> str:
        parts = series.astype(str).str.strip()
        parts = parts[parts.str.len() > 0].unique()
        return " | ".join(sorted(parts)) if len(parts) else ""

    result = (
        df.groupby(["rama", "dispensing_date"], sort=False, as_index=False)
        .agg(
            name_clean    = ("name_clean",    "first"),
            pharmacy_name = ("pharmacy_name", "first"),
            drugs         = ("drug",          _join_drugs),
        )
    )
    return result.reset_index(drop=True)


# ──────────────────────────────────────────────
# FRAUD ANALYSIS ENGINE
# ──────────────────────────────────────────────
def _closest_visit(
    rama: str,
    disp_dt: pd.Timestamp,
    clinic_index: dict[str, list[dict]],
) -> dict | None:
    """Return the clinic visit record closest in time to the dispensing date."""
    visits = clinic_index.get(rama, [])
    if not visits:
        return None
    return min(visits, key=lambda v: abs((v["visit_date"] - disp_dt).days))


def run_analysis(
    pharmacy: pd.DataFrame,
    clinic:   pd.DataFrame,
    name_threshold: int,
) -> pd.DataFrame:
    # Build index: RAMA → list of clinic visit dicts
    clinic_index: dict[str, list[dict]] = {}
    for row in clinic.to_dict("records"):
        clinic_index.setdefault(row["rama"], []).append(row)

    records = []
    for row in pharmacy.to_dict("records"):
        rama       = row["rama"]
        p_name     = row["name_clean"]
        disp_dt    = row["dispensing_date"]
        drugs      = row["drugs"]
        pharm_nm   = row["pharmacy_name"]

        best_visit  = _closest_visit(rama, disp_dt, clinic_index)

        score       = 0
        flags: list[str] = []

        # Defaults (set when NOT_FOUND)
        visit_found = "NOT_FOUND"
        clinic_name = ""
        name_sim    = None
        name_match  = None
        date_diff   = None
        date_flag   = ""
        clinic_dt   = None
        doctor      = ""

        if best_visit is None:
            # ── Fraud signal 1: Ghost patient ─────────────────────────
            score       += SCORE_NO_CLINIC_VISIT
            flags.append("NO_CLINIC_VISIT")
        else:
            visit_found  = "FOUND"
            clinic_dt    = best_visit["visit_date"]
            clinic_name  = best_visit["name_clean"]
            doctor       = best_visit.get("doctor", "")

            # ── Name fuzzy match (token_sort handles transposed names) ─
            name_sim   = rfuzz.token_sort_ratio(p_name, clinic_name)
            name_match = "MATCH" if name_sim >= name_threshold else "MISMATCH"
            if name_match == "MISMATCH":
                score += SCORE_NAME_MISMATCH
                flags.append("NAME_MISMATCH")

            # ── Fraud signal 2: Dispense before visit ─────────────────
            if pd.notna(disp_dt) and pd.notna(clinic_dt):
                date_diff = int((disp_dt - clinic_dt).days)
                if date_diff < 0:
                    score     += SCORE_DISPENSE_BEFORE_VISIT
                    flags.append("DISPENSE_BEFORE_VISIT")
                    date_flag  = "DISPENSE_BEFORE_VISIT"
                else:
                    date_flag = "OK"

        risk_level = (
            "HIGH"   if score >= 50 else
            "MEDIUM" if score >= 20 else
            "LOW"
        )

        records.append({
            "RAMA":              rama,
            "Pharmacy Name":     p_name,
            "Clinic Name":       clinic_name,
            "Pharmacy":          pharm_nm,
            "Drugs Dispensed":   drugs,
            "Dispensing Date":   disp_dt.date() if pd.notna(disp_dt) else "",
            "Clinic Visit Date": clinic_dt.date() if clinic_dt is not None and pd.notna(clinic_dt) else "",
            "Date Diff (days)":  date_diff,
            "Date Flag":         date_flag,
            "Visit Found":       visit_found,
            "Name Similarity":   name_sim,
            "Name Match":        name_match,
            "Doctor":            doctor,
            "Fraud Score":       score,
            "Risk Level":        risk_level,
            "Flags":             ", ".join(flags) if flags else "NONE",
        })

    return pd.DataFrame(records)


# ──────────────────────────────────────────────
# DOCTOR–PATIENT NETWORK GRAPH
# Nodes:  Doctor (diamond, blue) · Patient (circle, coloured by risk)
# Edges:  one per Doctor–Patient pair, labelled with drugs prescribed
# ──────────────────────────────────────────────
def build_network_graph(results: pd.DataFrame) -> go.Figure:
    df_g = results[
        (results["Visit Found"] == "FOUND") &
        (results["Doctor"].astype(str).str.strip() != "")
    ].copy()

    _empty_layout = dict(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        height=420,
        xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
        yaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
    )

    if df_g.empty:
        fig = go.Figure()
        fig.add_annotation(
            text="No network data — map the doctor column to enable this view.",
            xref="paper", yref="paper", x=0.5, y=0.5,
            showarrow=False, font=dict(color="#888", size=13),
        )
        fig.update_layout(**_empty_layout)
        return fig

    G = nx.Graph()

    for _, row in df_g.iterrows():
        pid      = row["RAMA"]
        doc_id   = f"DR:{row['Doctor']}"
        drugs    = row["Drugs Dispensed"]
        risk     = row["Risk Level"]
        p_name   = row["Pharmacy Name"]
        doc_name = row["Doctor"]

        G.add_node(pid,    ntype="patient", risk=risk,  label=p_name)
        G.add_node(doc_id, ntype="doctor",  risk="NA",  label=doc_name)

        if G.has_edge(pid, doc_id):
            existing = G[pid][doc_id]["drugs"]
            if drugs not in existing:
                G[pid][doc_id]["drugs"] = existing + " | " + drugs
        else:
            G.add_edge(pid, doc_id, drugs=drugs)

    pos = nx.spring_layout(G, seed=42, k=3.0)

    patients = [(n, d) for n, d in G.nodes(data=True) if d["ntype"] == "patient"]
    doctors  = [(n, d) for n, d in G.nodes(data=True) if d["ntype"] == "doctor"]

    # Edges
    edge_x: list[float | None] = []
    edge_y: list[float | None] = []
    for u, v in G.edges():
        x0, y0 = pos[u]; x1, y1 = pos[v]
        edge_x += [x0, x1, None]
        edge_y += [y0, y1, None]

    edge_trace = go.Scatter(
        x=edge_x, y=edge_y, mode="lines",
        line=dict(width=1, color="#3a3a3a"),
        hoverinfo="none", showlegend=False,
    )

    # Patient nodes (coloured by risk)
    _c = {"HIGH": "#FF4B4B", "MEDIUM": "#FFA600", "LOW": "#21C354"}
    p_x      = [pos[n][0] for n, _ in patients]
    p_y      = [pos[n][1] for n, _ in patients]
    p_color  = [_c.get(d["risk"], "#888") for _, d in patients]
    p_hover  = [
        f"RAMA: {n}<br>Name: {d['label']}<br>Risk: {d['risk']}"
        for n, d in patients
    ]
    patient_trace = go.Scatter(
        x=p_x, y=p_y, mode="markers",
        marker=dict(size=12, color=p_color, line=dict(width=1, color="#111")),
        text=p_hover, hoverinfo="text",
        name="Patient",
    )

    # Doctor nodes (blue diamonds with name labels)
    d_x    = [pos[n][0] for n, _ in doctors]
    d_y    = [pos[n][1] for n, _ in doctors]
    d_text = [d["label"] for _, d in doctors]
    d_hover = [f"Doctor: {d['label']}" for _, d in doctors]
    doctor_trace = go.Scatter(
        x=d_x, y=d_y, mode="markers+text",
        marker=dict(size=22, color="#4A9EF5", symbol="diamond",
                    line=dict(width=1.5, color="#fff")),
        text=d_text, textposition="top center",
        textfont=dict(size=10, color="#ccc"),
        hovertext=d_hover, hoverinfo="text",
        name="Doctor",
    )

    # Drug labels at edge midpoints
    lx, ly, lt = [], [], []
    for u, v, data in G.edges(data=True):
        x0, y0 = pos[u]; x1, y1 = pos[v]
        lx.append((x0 + x1) / 2)
        ly.append((y0 + y1) / 2)
        s = data["drugs"]
        lt.append(s[:40] + "…" if len(s) > 40 else s)

    label_trace = go.Scatter(
        x=lx, y=ly, mode="text",
        text=lt, textfont=dict(size=7, color="#555"),
        hoverinfo="none", showlegend=False,
    )

    fig = go.Figure(
        data=[edge_trace, patient_trace, doctor_trace, label_trace],
        layout=go.Layout(
            template="plotly_dark",
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            hovermode="closest",
            showlegend=True,
            height=560,
            margin=dict(l=5, r=5, t=15, b=5),
            xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
            yaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
            legend=dict(
                bgcolor="rgba(0,0,0,0.35)",
                bordercolor="#333",
                borderwidth=1,
                font=dict(color="#aaa"),
            ),
        ),
    )
    return fig


# ──────────────────────────────────────────────
# DATAFRAME STYLING
# ──────────────────────────────────────────────
def _style_risk(val: str) -> str:
    return {
        "HIGH":   "color: #FF4B4B; font-weight: bold",
        "MEDIUM": "color: #FFA600; font-weight: bold",
        "LOW":    "color: #21C354",
    }.get(str(val), "")


def _style_visit(val: str) -> str:
    return {
        "NOT_FOUND": "color: #FF4B4B; font-weight: bold",
        "FOUND":     "color: #21C354",
    }.get(str(val), "")


# ──────────────────────────────────────────────
# STREAMLIT UI
# ──────────────────────────────────────────────
st.set_page_config(
    page_title="RSSB Fraud Detection",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("🔍 RSSB Medical Claim Fraud Detection")
st.caption(
    "Cross-check pharmacy dispensing records against clinic consultation registers "
    "to identify ghost claims, suspicious dispensing patterns, and provider relationships."
)


# ── Sidebar ───────────────────────────────────
with st.sidebar:
    st.header("📂 Upload Files")
    clinic_file   = st.file_uploader("Clinic consultation report",  type=["csv", "xlsx", "xls"])
    pharmacy_file = st.file_uploader("Pharmacy dispensing report",  type=["csv", "xlsx", "xls"])

    st.divider()
    st.header("⚙️ Analysis Settings")
    name_threshold = st.slider(
        "Name similarity threshold", min_value=50, max_value=100,
        value=DEFAULT_NAME_THRESHOLD, step=1,
        help=(
            "Minimum RapidFuzz token_sort_ratio score (0–100) for two names to be "
            "considered a MATCH. Lower = more lenient, higher = stricter."
        ),
    )
    st.caption(
        f"Score ≥ {name_threshold} → MATCH  |  "
        f"Score < {name_threshold} → MISMATCH (+{SCORE_NAME_MISMATCH} pts)"
    )

    st.divider()
    st.markdown(
        "**Scoring model**\n\n"
        f"| Rule | Points |\n|---|---|\n"
        f"| No clinic visit | +{SCORE_NO_CLINIC_VISIT} |\n"
        f"| Name mismatch | +{SCORE_NAME_MISMATCH} |\n"
        f"| Dispense before visit | +{SCORE_DISPENSE_BEFORE_VISIT} |\n\n"
        "**Risk levels**\n\n"
        "🔴 HIGH ≥ 50 · 🟡 MEDIUM ≥ 20 · 🟢 LOW < 20"
    )


# ── Column mapping helper ──────────────────────
def _mapping_ui(
    prefix: str,
    columns: list[str],
    required: list[tuple[str, str]],   # (key, label)
    optional: list[tuple[str, str]],
    hints: dict[str, list[str]],
) -> dict[str, str]:
    detected = auto_detect(columns, hints)
    mapping: dict[str, str] = {}

    req_cols = st.columns(len(required))
    for idx, (key, label) in enumerate(required):
        opts  = [""] + columns
        pref  = detected.get(key)
        index = opts.index(pref) if pref in opts else 0
        with req_cols[idx]:
            mapping[key] = st.selectbox(
                f"{label} *", opts, index=index, key=f"{prefix}_{key}"
            )

    if optional:
        with st.expander("Optional fields"):
            opt_cols = st.columns(len(optional))
            for idx, (key, label) in enumerate(optional):
                opts  = [""] + columns
                pref  = detected.get(key)
                index = opts.index(pref) if pref in opts else 0
                with opt_cols[idx]:
                    mapping[key] = st.selectbox(
                        label, opts, index=index, key=f"{prefix}_{key}_opt"
                    )

    return mapping


# ── Main flow ─────────────────────────────────
if clinic_file and pharmacy_file:
    clinic_raw   = load_file(clinic_file.name,   clinic_file.getvalue())
    pharmacy_raw = load_file(pharmacy_file.name, pharmacy_file.getvalue())

    c_cols = clinic_raw.columns.tolist()
    p_cols = pharmacy_raw.columns.tolist()

    # ── Column Mapping ────────────────────────
    st.subheader("① Column Mapping")
    st.caption("Map your file columns to the required fields. Auto-detection applied where possible.")

    cm1, cm2 = st.columns(2)
    with cm1:
        st.markdown("**Clinic file**")
        clinic_map = _mapping_ui(
            "clinic", c_cols,
            required=[("rama", "RAMA number"), ("name", "Patient name"), ("visit_date", "Visit date")],
            optional=[("doctor", "Doctor name")],
            hints=_CLINIC_HINTS,
        )
    with cm2:
        st.markdown("**Pharmacy file**")
        pharmacy_map = _mapping_ui(
            "pharmacy", p_cols,
            required=[("rama", "RAMA number"), ("name", "Patient name"), ("dispensing_date", "Dispensing date")],
            optional=[("drug", "Drug name"), ("pharmacy_name", "Pharmacy name")],
            hints=_PHARMACY_HINTS,
        )

    req_c = all(clinic_map.get(f) for f in ["rama", "name", "visit_date"])
    req_p = all(pharmacy_map.get(f) for f in ["rama", "name", "dispensing_date"])

    # ── Data previews ─────────────────────────
    with st.expander("Preview clinic data (first 5 rows)"):
        st.dataframe(clinic_raw.head(5), use_container_width=True)
    with st.expander("Preview pharmacy data (first 5 rows — raw, before normalisation)"):
        st.dataframe(pharmacy_raw.head(5), use_container_width=True)

    st.divider()

    # ── Run button ────────────────────────────
    if st.button("🔎 Run Fraud Analysis", type="primary", disabled=not (req_c and req_p)):
        with st.spinner("Cleaning data…"):
            clinic_clean    = clean_clinic(clinic_raw,   clinic_map)
            pharm_cleaned   = clean_pharmacy(pharmacy_raw, pharmacy_map)

        with st.spinner("Normalising pharmacy records (collapsing multi-drug visits)…"):
            pharm_norm = normalise_pharmacy(pharm_cleaned)

        with st.spinner("Running cross-check and scoring…"):
            results = run_analysis(pharm_norm, clinic_clean, name_threshold)

        st.session_state["results"]        = results
        st.session_state["raw_pharm_count"] = len(pharm_cleaned)
        st.session_state["norm_pharm_count"] = len(pharm_norm)

    # ── Results ───────────────────────────────
    results: pd.DataFrame | None = st.session_state.get("results")

    if results is not None:
        raw_count  = st.session_state.get("raw_pharm_count", 0)
        norm_count = st.session_state.get("norm_pharm_count", 0)

        # Normalisation note
        if raw_count != norm_count:
            st.info(
                f"ℹ️ **Pharmacy normalisation:** {raw_count} raw dispensing rows → "
                f"**{norm_count} unique visits** after collapsing multi-drug entries per visit."
            )

        # ── Summary metrics ───────────────────
        st.subheader("② Summary")
        total   = len(results)
        found   = int((results["Visit Found"] == "FOUND").sum())
        not_fnd = int((results["Visit Found"] == "NOT_FOUND").sum())
        high    = int((results["Risk Level"] == "HIGH").sum())
        medium  = int((results["Risk Level"] == "MEDIUM").sum())
        low     = int((results["Risk Level"] == "LOW").sum())

        m1, m2, m3, m4, m5, m6 = st.columns(6)
        m1.metric("Total Claims (normalised)", total)
        m2.metric("✅ Found in Clinic",         found)
        m3.metric("❌ Not Found",               not_fnd)
        m4.metric("🔴 High Risk",               high)
        m5.metric("🟡 Medium Risk",             medium)
        m6.metric("🟢 Low Risk",                low)

        # ── Tabs ──────────────────────────────
        tab1, tab2, tab3, tab4 = st.tabs([
            "📋 Flagged Records",
            "📈 Risk Charts",
            "🕸️ Network Graph",
            "💾 Export",
        ])

        # ── TAB 1: Flagged Records ─────────────
        with tab1:
            fc1, fc2, fc3 = st.columns([2, 2, 2])
            with fc1:
                risk_filter = st.multiselect(
                    "Risk level", ["HIGH", "MEDIUM", "LOW"],
                    default=["HIGH", "MEDIUM", "LOW"],
                )
            with fc2:
                visit_filter = st.multiselect(
                    "Clinic status", ["FOUND", "NOT_FOUND"],
                    default=["FOUND", "NOT_FOUND"],
                )
            with fc3:
                sort_col = st.selectbox(
                    "Sort by",
                    list(_SORT_ASCENDING.keys()),
                )

            df_view = (
                results[
                    results["Risk Level"].isin(risk_filter) &
                    results["Visit Found"].isin(visit_filter)
                ]
                .sort_values(
                    sort_col,
                    ascending=_SORT_ASCENDING[sort_col],
                    na_position="last",
                )
            )

            st.caption(f"Showing **{len(df_view)}** of {total} records")
            st.dataframe(
                df_view.style
                    .map(_style_risk,  subset=["Risk Level"])
                    .map(_style_visit, subset=["Visit Found"]),
                use_container_width=True,
                height=440,
            )

        # ── TAB 2: Charts ─────────────────────
        with tab2:
            ch1, ch2 = st.columns(2)

            with ch1:
                # Risk level bar chart
                risk_counts = (
                    results["Risk Level"]
                    .value_counts()
                    .reindex(["HIGH", "MEDIUM", "LOW"], fill_value=0)
                    .reset_index()
                )
                risk_counts.columns = ["Risk Level", "Count"]
                fig_bar = px.bar(
                    risk_counts, x="Risk Level", y="Count",
                    color="Risk Level",
                    color_discrete_map=RISK_COLORS,
                    template="plotly_dark",
                    title="Claims by Risk Level",
                    text="Count",
                )
                fig_bar.update_traces(textposition="outside")
                fig_bar.update_layout(
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0.15)",
                    showlegend=False,
                )
                st.plotly_chart(fig_bar, use_container_width=True)

            with ch2:
                # Fraud score distribution
                fig_hist = px.histogram(
                    results, x="Fraud Score", nbins=15,
                    color_discrete_sequence=["#4A9EF5"],
                    template="plotly_dark",
                    title="Fraud Score Distribution",
                )
                fig_hist.update_layout(
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0.15)",
                    bargap=0.05,
                )
                st.plotly_chart(fig_hist, use_container_width=True)

            ch3, ch4 = st.columns(2)

            with ch3:
                # Clinic match donut
                visit_counts = results["Visit Found"].value_counts().reset_index()
                visit_counts.columns = ["Status", "Count"]
                fig_donut = px.pie(
                    visit_counts, names="Status", values="Count",
                    color="Status",
                    color_discrete_map={"FOUND": "#21C354", "NOT_FOUND": "#FF4B4B"},
                    template="plotly_dark",
                    title="Clinic Visit Match Rate",
                    hole=0.45,
                )
                fig_donut.update_layout(paper_bgcolor="rgba(0,0,0,0)")
                st.plotly_chart(fig_donut, use_container_width=True)

            with ch4:
                # Flags breakdown
                flag_series = (
                    results["Flags"]
                    .str.split(", ")
                    .explode()
                    .value_counts()
                    .reset_index()
                )
                flag_series.columns = ["Flag", "Count"]
                flag_series = flag_series[flag_series["Flag"] != "NONE"]
                if not flag_series.empty:
                    fig_flags = px.bar(
                        flag_series, x="Flag", y="Count",
                        color_discrete_sequence=["#FF6B6B"],
                        template="plotly_dark",
                        title="Fraud Flag Frequency",
                        text="Count",
                    )
                    fig_flags.update_traces(textposition="outside")
                    fig_flags.update_layout(
                        paper_bgcolor="rgba(0,0,0,0)",
                        plot_bgcolor="rgba(0,0,0,0.15)",
                        showlegend=False,
                    )
                    st.plotly_chart(fig_flags, use_container_width=True)
                else:
                    st.info("No fraud flags recorded.")

        # ── TAB 3: Network Graph ──────────────
        with tab3:
            st.markdown(
                "**Doctor–Patient Prescription Network**\n\n"
                "🔷 Doctor nodes (diamond, blue) · Patient nodes (circle, coloured by risk level) · "
                "Edges labelled with drugs prescribed.\n\n"
                "Requires the **doctor** optional column to be mapped in the clinic file."
            )
            fig_net = build_network_graph(results)
            st.plotly_chart(fig_net, use_container_width=True)

            # Companion table: doctor → patients + drugs
            doc_data = results[
                (results["Visit Found"] == "FOUND") &
                (results["Doctor"].astype(str).str.strip() != "")
            ][["Doctor", "RAMA", "Pharmacy Name", "Drugs Dispensed", "Risk Level"]].copy()

            if not doc_data.empty:
                with st.expander("Doctor prescription table"):
                    st.dataframe(
                        doc_data.sort_values("Doctor").style.map(
                            _style_risk, subset=["Risk Level"]
                        ),
                        use_container_width=True,
                    )

        # ── TAB 4: Export ─────────────────────
        with tab4:
            st.subheader("Download Investigation Reports")

            col_a, col_b = st.columns(2)

            # Full report
            buf_full = io.StringIO()
            results.to_csv(buf_full, index=False)
            with col_a:
                st.download_button(
                    "⬇ Full Report — all claims (CSV)",
                    buf_full.getvalue().encode("utf-8"),
                    file_name="rssb_fraud_full_report.csv",
                    mime="text/csv",
                    use_container_width=True,
                )

            # High-risk only
            high_df = results[results["Risk Level"] == "HIGH"]
            if not high_df.empty:
                buf_high = io.StringIO()
                high_df.to_csv(buf_high, index=False)
                with col_b:
                    st.download_button(
                        f"⬇ High Risk Only — {len(high_df)} records (CSV)",
                        buf_high.getvalue().encode("utf-8"),
                        file_name="rssb_high_risk.csv",
                        mime="text/csv",
                        use_container_width=True,
                    )

            st.divider()
            st.subheader("Report Preview")
            st.dataframe(
                results.style
                    .map(_style_risk,  subset=["Risk Level"])
                    .map(_style_visit, subset=["Visit Found"]),
                use_container_width=True,
                height=380,
            )

else:
    st.info(
        "👆 Upload both the **clinic consultation report** and the "
        "**pharmacy dispensing report** in the sidebar to begin."
    )
    # Clear stale results when files are removed
    for _k in ("results", "raw_pharm_count", "norm_pharm_count"):
        st.session_state.pop(_k, None)

st.divider()
st.caption(
    "RSSB Fraud Detection System  •  "
    "Requirements: streamlit · pandas · openpyxl · rapidfuzz · networkx · plotly"
)
