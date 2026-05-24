# aether/tools/render_visual.py

"""Deterministic tool that emits a Vega-Lite spec from already-computed findings.

Grounding by construction: the data values in the spec are copied verbatim from
the tool_args (which the loop agent assembled from prior tool observations).
This tool does NO LLM calls and invents NO data — it is a pure formatter.
If the provided data is empty or malformed, it returns insufficient_data=true.
"""

from aether.tools.base import BaseTool


class RenderVisualTool(BaseTool):
    name = "render_visual"

    def run(self, args: dict) -> dict:
        """Build a grouped/comparison bar chart Vega-Lite spec.

        Expected args:
            title       (str):  chart title
            x_field     (str):  name of the category field (x-axis)
            y_field     (str):  name of the numeric field (y-axis)
            data        (list[dict]): rows — each dict must contain x_field and y_field
            color_field (str, optional): field for grouped bars (legend/color)
            source_step (str, optional): which prior step the data came from

        Returns:
            {
                "vega_lite_spec":       dict | None,
                "grounded":             bool,
                "insufficient_data":    bool,
                "source_findings_ref":  str,
            }
        """
        title = args.get("title", "Chart")
        x_field = args.get("x_field", "")
        y_field = args.get("y_field", "")
        data = args.get("data", [])
        color_field = args.get("color_field")
        source_step = args.get("source_step", "unknown")

        # ── Guard: reject if data is missing or fields are absent ────────
        if not x_field or not y_field:
            return {
                "vega_lite_spec": None,
                "grounded": False,
                "insufficient_data": True,
                "source_findings_ref": source_step,
                "reason": "x_field and y_field are required",
            }

        if not isinstance(data, list) or len(data) == 0:
            return {
                "vega_lite_spec": None,
                "grounded": False,
                "insufficient_data": True,
                "source_findings_ref": source_step,
                "reason": "data list is empty or missing",
            }

        # Validate every row has the required fields with a numeric y value
        clean_rows = []
        for row in data:
            if not isinstance(row, dict):
                continue
            if x_field not in row or y_field not in row:
                continue
            try:
                y_val = float(row[y_field])
            except (TypeError, ValueError):
                continue
            clean_row = {x_field: row[x_field], y_field: y_val}
            if color_field and color_field in row:
                clean_row[color_field] = row[color_field]
            clean_rows.append(clean_row)

        if not clean_rows:
            return {
                "vega_lite_spec": None,
                "grounded": False,
                "insufficient_data": True,
                "source_findings_ref": source_step,
                "reason": "no valid rows after validation (missing fields or non-numeric y values)",
            }

        # ── Build Vega-Lite spec ─────────────────────────────────────────
        encoding = {
            "x": {"field": x_field, "type": "nominal", "axis": {"labelAngle": -45}},
            "y": {"field": y_field, "type": "quantitative"},
        }
        if color_field:
            encoding["color"] = {"field": color_field, "type": "nominal"}
            encoding["xOffset"] = {"field": color_field}

        spec = {
            "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
            "title": title,
            "width": 500,
            "height": 300,
            "data": {"values": clean_rows},
            "mark": {"type": "bar", "cornerRadiusTopLeft": 3, "cornerRadiusTopRight": 3},
            "encoding": encoding,
        }

        return {
            "vega_lite_spec": spec,
            "grounded": True,
            "insufficient_data": False,
            "source_findings_ref": source_step,
        }
