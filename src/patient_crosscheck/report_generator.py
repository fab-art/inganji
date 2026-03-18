from __future__ import annotations

import csv
from pathlib import Path
from typing import Dict, Iterable, List

from .anomaly_detector import Anomaly


class ReportGenerator:
    def generate_summary_report(self, anomalies: Iterable[Anomaly]) -> List[dict]:
        return [a.__dict__ for a in anomalies]

    def generate_statistics(self, anomalies: List[Anomaly], total_analyzed: int) -> Dict[str, int]:
        stats: Dict[str, int] = {"total_analyzed": total_analyzed, "total_anomalies": len(anomalies)}
        for sev in ("Critical", "High", "Medium", "Low"):
            stats[f"severity_{sev.lower()}"] = sum(1 for a in anomalies if a.severity == sev)
        return stats

    def create_excel_report(self, data: List[dict], output_path: str | Path) -> bool:
        path = Path(output_path)
        if not data:
            path.write_text("")
            return True

        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(data[0].keys()))
            writer.writeheader()
            writer.writerows(data)
        return True
