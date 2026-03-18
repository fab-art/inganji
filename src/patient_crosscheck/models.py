from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class AnalysisConfig:
    """Top-level analysis configuration."""

    max_file_size_mb: int = 100
    max_date_gap_days: int = 30
    fuzzy_name_threshold: float = 85.0


@dataclass(frozen=True)
class FileMetadata:
    """Metadata returned after file validation and ingestion."""

    file_path: Path
    file_size_bytes: int
    row_count: int
    column_count: int
    min_date: Optional[datetime]
    max_date: Optional[datetime]
