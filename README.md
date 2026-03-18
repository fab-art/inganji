# Patient Cross-Checking System (Streamlit)

A Streamlit-based app for validating pharmacy dispensing records against clinic visit records and flagging potential anomalies.

## What this version includes
- Streamlit UI with file upload for pharmacy + clinic datasets (`.csv`, `.xlsx`, `.xls`)
- Assisted column mapping for `patient_id`, `patient_name`, and `visit_date`
- RAMA-first matching with name similarity and date-window checks
- Anomaly classification (Critical/High/Medium)
- Interactive anomaly table + CSV download
- CLI helper (`patient-crosscheck`) for local file metadata checks

## Local development
```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
PYTHONPATH=src pytest -q
streamlit run app.py
```

## Streamlit deployment
This repo is ready for Streamlit Community Cloud deployment:
1. Set app entrypoint to `app.py`
2. Python version: 3.9+
3. Install dependencies from `pyproject.toml`

## Project structure
- `app.py` – Streamlit web UI
- `src/patient_crosscheck/` – core analysis modules
- `tests/` – unit tests for file handling, mapping, and matching
