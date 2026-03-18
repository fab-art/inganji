from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Dict, Iterable

REQUIRED_FIELDS = ("patient_id", "patient_name", "visit_date")

KEYWORDS = {
    "patient_id": ["rama", "rama number", "patient_rama_number", "id"],
    "patient_name": ["name", "beneficiary", "patient_name"],
    "visit_date": ["date", "dispensing_date", "prescription_date", "visit_date"],
}


@dataclass(frozen=True)
class MappingSuggestion:
    field: str
    column: str
    score: float


class ColumnMapper:
    def suggest_mapping(self, column_names: Iterable[str]) -> Dict[str, MappingSuggestion]:
        cols = [str(c).strip() for c in column_names]
        suggestions: Dict[str, MappingSuggestion] = {}

        for field, keywords in KEYWORDS.items():
            best_col = ""
            best_score = -1.0
            for col in cols:
                score = max(self._similarity(col.lower(), kw.lower()) for kw in keywords)
                if score > best_score:
                    best_col = col
                    best_score = score
            if best_col:
                suggestions[field] = MappingSuggestion(field, best_col, best_score)

        return suggestions

    def auto_detect_columns(self, column_names: Iterable[str]) -> Dict[str, str]:
        return {
            field: suggestion.column
            for field, suggestion in self.suggest_mapping(column_names).items()
            if suggestion.score >= 70
        }

    def validate_mapping(self, mapping: Dict[str, str]) -> bool:
        return all(field in mapping and mapping[field] for field in REQUIRED_FIELDS)

    @staticmethod
    def _similarity(a: str, b: str) -> float:
        return SequenceMatcher(None, a, b).ratio() * 100.0
