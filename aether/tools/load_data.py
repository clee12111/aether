# aether/tools/load_data.py

from pathlib import Path

import pandas as pd

from aether.config import settings
from aether.tools.base import BaseTool


class LoadDataTool(BaseTool):
    name = "load_data"

    # Shared registry so RunSQLTool can access loaded frames
    _registry: dict[str, pd.DataFrame] = {}

    def reset(self) -> None:
        LoadDataTool._registry.clear()

    def run(self, args: dict) -> dict:
        file_path = _resolve_path(args["file_path"])
        table_name = args["table_name"]

        if file_path.suffix.lower() in {".xlsx", ".xls"}:
            df = pd.read_excel(file_path)
        else:
            df = pd.read_csv(file_path)

        LoadDataTool._registry[table_name] = df
        return {
            "table_name": table_name,
            "row_count": len(df),
            "columns": df.columns.tolist(),
        }


def _resolve_path(file_path: str) -> Path:
    p = Path(file_path)
    if p.is_absolute() or p.exists():
        return p
    for base in (settings.data_demo_dir, settings.data_upload_dir):
        candidate = Path(base) / p.name
        if candidate.exists():
            return candidate
    return p  # let pandas raise a natural FileNotFoundError
