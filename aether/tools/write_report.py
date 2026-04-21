# aether/tools/write_report.py

import json
from pathlib import Path

from aether.tools.base import BaseTool

_UPLOAD_DIR = Path("data/uploads")


class WriteReportTool(BaseTool):
    name = "write_report"

    def run(self, args: dict) -> dict:
        title = args["title"]
        fmt = args.get("format", "json").lower()
        results = args.get("results") or args.get("prior_results", {})

        _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

        if fmt == "json":
            out_path = _UPLOAD_DIR / f"{title}.json"
            out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
        else:
            out_path = _UPLOAD_DIR / f"{title}.txt"
            out_path.write_text(str(results), encoding="utf-8")

        return {"path": str(out_path), "format": fmt}
