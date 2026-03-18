from __future__ import annotations

from dataclasses import dataclass
from typing import List

from .patient_matcher import MatchResult


@dataclass(frozen=True)
class Anomaly:
    pharmacy_index: int
    kind: str
    severity: str
    details: str


class AnomalyDetector:
    def find_unmatched_pharmacy_visits(self, matches: List[MatchResult]) -> List[Anomaly]:
        anomalies: List[Anomaly] = []
        for m in matches:
            if not m.rama_match or m.clinic_index is None:
                anomalies.append(
                    Anomaly(
                        pharmacy_index=m.pharmacy_index,
                        kind="unmatched_pharmacy_visit",
                        severity="Critical",
                        details="No clinic record found for RAMA number",
                    )
                )
        return anomalies

    def detect_date_anomalies(self, matches: List[MatchResult]) -> List[Anomaly]:
        anomalies: List[Anomaly] = []
        for m in matches:
            if m.clinic_index is not None and not m.date_valid:
                anomalies.append(
                    Anomaly(
                        pharmacy_index=m.pharmacy_index,
                        kind="invalid_date_sequence",
                        severity="High",
                        details="Clinic date is after dispensing or exceeds configured date window",
                    )
                )
        return anomalies

    def check_name_discrepancies(self, matches: List[MatchResult], threshold: float = 85.0) -> List[Anomaly]:
        anomalies: List[Anomaly] = []
        for m in matches:
            if m.clinic_index is not None and m.name_similarity < threshold:
                anomalies.append(
                    Anomaly(
                        pharmacy_index=m.pharmacy_index,
                        kind="name_mismatch",
                        severity="Medium",
                        details=f"Name similarity below threshold: {m.name_similarity:.2f}",
                    )
                )
        return anomalies
