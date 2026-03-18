from pathlib import Path


def test_app_contains_single_file_service_classes() -> None:
    app_text = Path("app.py").read_text(encoding="utf-8")
    for symbol in [
        "class FileHandler:",
        "class ColumnMapper:",
        "class PatientMatcher:",
        "class AnomalyDetector:",
        "class ReportGenerator:",
        "class AnalysisService:",
    ]:
        assert symbol in app_text


def test_app_runs_streamlit_from_single_entrypoint() -> None:
    app_text = Path("app.py").read_text(encoding="utf-8")
    assert "def render_app() -> None:" in app_text
    assert 'if __name__ == "__main__":' in app_text
