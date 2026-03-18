from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from typing import Dict, List, Optional

from .models import AnalysisConfig


@dataclass(frozen=True)
class MatchResult:
    pharmacy_index: int
    clinic_index: Optional[int]
    rama_match: bool
    name_similarity: float
    date_valid: bool
    confidence: float


class PatientMatcher:
    def __init__(self, config: Optional[AnalysisConfig] = None) -> None:
        self.config = config or AnalysisConfig()

    @staticmethod
    def fuzzy_name_match(name1: str, name2: str) -> float:
        return SequenceMatcher(None, str(name1), str(name2)).ratio() * 100.0

    def validate_date_sequence(self, dispensing_date: datetime, clinic_date: datetime) -> bool:
        delta = (dispensing_date - clinic_date).days
        return 0 <= delta <= self.config.max_date_gap_days

    def calculate_confidence_score(self, rama_match: bool, name_similarity: float, date_valid: bool) -> float:
        score = (70 if rama_match else 0) + (name_similarity * 0.2) + (10 if date_valid else 0)
        return round(min(score, 100.0), 2)

    def match_by_rama(
        self,
        pharmacy_rows: List[Dict[str, object]],
        clinic_rows: List[Dict[str, object]],
        pharmacy_map: Dict[str, str],
        clinic_map: Dict[str, str],
    ) -> List[MatchResult]:
        clinic_index: Dict[str, List[tuple[int, Dict[str, object]]]] = {}
        for idx, row in enumerate(clinic_rows):
            key = str(row.get(clinic_map["patient_id"], "")).strip()
            clinic_index.setdefault(key, []).append((idx, row))

        results: List[MatchResult] = []
        for p_idx, p_row in enumerate(pharmacy_rows):
            rama = str(p_row.get(pharmacy_map["patient_id"], "")).strip()
            if not rama or rama not in clinic_index:
                results.append(MatchResult(p_idx, None, False, 0.0, False, 0.0))
                continue

            best = None
            for c_idx, c_row in clinic_index[rama]:
                sim = self.fuzzy_name_match(
                    str(p_row.get(pharmacy_map["patient_name"], "")),
                    str(c_row.get(clinic_map["patient_name"], "")),
                )
                p_date = self._parse_date(p_row.get(pharmacy_map["visit_date"]))
                c_date = self._parse_date(c_row.get(clinic_map["visit_date"]))
                date_valid = bool(p_date and c_date and self.validate_date_sequence(p_date, c_date))
                conf = self.calculate_confidence_score(True, sim, date_valid)
                candidate = MatchResult(p_idx, c_idx, True, sim, date_valid, conf)
                if best is None or candidate.confidence > best.confidence:
                    best = candidate
            results.append(best or MatchResult(p_idx, None, False, 0.0, False, 0.0))

        return results

    @staticmethod
    def _parse_date(value: object) -> Optional[datetime]:
        if isinstance(value, datetime):
            return value
        if value is None:
            return None
        text = str(value).strip()
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                pass
        return None
