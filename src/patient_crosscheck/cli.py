from __future__ import annotations

import argparse
import json
from pathlib import Path

from .file_handler import FileHandler


def main() -> None:
    parser = argparse.ArgumentParser(description="Patient Cross-Checking System CLI")
    parser.add_argument("input_file", type=Path, help="Path to input Excel/CSV file")
    args = parser.parse_args()

    file_handler = FileHandler()
    metadata = file_handler.get_file_metadata(args.input_file)
    payload = {
        "file": str(metadata.file_path),
        "rows": metadata.row_count,
        "columns": metadata.column_count,
        "size_bytes": metadata.file_size_bytes,
        "min_date": metadata.min_date.isoformat() if metadata.min_date else None,
        "max_date": metadata.max_date.isoformat() if metadata.max_date else None,
    }
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
