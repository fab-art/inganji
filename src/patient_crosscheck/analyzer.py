from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from .anomaly_detector import Anomaly, AnomalyDetector
from .patient_matcher import MatchResult, PatientMatcher
from .report_generator import ReportGenerator


@dataclass(frozen=True)
class AnalysisResult:
    matches: List[MatchResult]
    anomalies: List[Anomaly]
    summary_rows: List[dict]
    stats: Dict[str, int]


class AnalysisService:
    def __init__(self) -> None:
        self.matcher = PatientMatcher()
        self.detector = AnomalyDetector()
        self.reporter = ReportGenerator()

    def run(
        self,
        pharmacy_rows: List[dict],
        clinic_rows: List[dict],
        pharmacy_map: Dict[str, str],
        clinic_map: Dict[str, str],
    ) -> AnalysisResult:
        matches = self.matcher.match_by_rama(pharmacy_rows, clinic_rows, pharmacy_map, clinic_map)

        anomalies = []
        anomalies.extend(self.detector.find_unmatched_pharmacy_visits(matches))
        anomalies.extend(self.detector.detect_date_anomalies(matches))
        anomalies.extend(
            self.detector.check_name_discrepancies(matches, threshold=self.matcher.config.fuzzy_name_threshold)
        )

        summary_rows = self.reporter.generate_summary_report(anomalies)
        stats = self.reporter.generate_statistics(anomalies, total_analyzed=len(pharmacy_rows))

        return AnalysisResult(matches=matches, anomalies=anomalies, summary_rows=summary_rows, stats=stats)
