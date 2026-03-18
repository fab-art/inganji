# Patient Cross-Checking System

This project is now implemented as a **single-file Streamlit application** in `app.py`.

## What is included
- Streamlit user interface for uploading pharmacy and clinic files
- In-file `FileHandler`, `ColumnMapper`, `PatientMatcher`, `AnomalyDetector`, `ReportGenerator`, and `AnalysisService`
- CSV and Excel ingestion (`.csv`, `.xlsx`, `.xls`)
- RAMA-based cross-checking, date validation, anomaly detection, and CSV export
- No local package import dependency for deployment; the app is self-contained

## Run locally
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest -q
streamlit run app.py
```

## Deploy on Streamlit
- App entrypoint: `app.py`
- Python version: 3.9+
- Dependencies are defined in `requirements.txt`
