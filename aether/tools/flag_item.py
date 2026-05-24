# aether/tools/flag_item.py

from aether.tools.base import BaseTool

_VALID_SEVERITIES = {"low", "medium", "high"}

_ID_KEYS = ("item_id", "entity", "partner", "name", "partner_name")


class FlagItemTool(BaseTool):
    name = "flag_item"

    def __init__(self) -> None:
        self._flags: list[dict] = []

    def run(self, args: dict) -> dict:
        severity = str(args.get("severity", "medium")).lower()
        if severity not in _VALID_SEVERITIES:
            severity = "medium"

        reason = str(
            args.get("reason") or args.get("description") or "No reason provided"
        )

        # Check for explicit item_id first — single-item path takes priority
        explicit_id = None
        for key in _ID_KEYS:
            val = args.get(key)
            if val:
                explicit_id = str(val)
                break

        if explicit_id:
            self._flags.append({"item_id": explicit_id, "reason": reason, "severity": severity})
            return {"flagged": True, "item_id": explicit_id, "total_flagged": 1}

        # Bulk path: flag rows from SQL results in prior_results
        prior = args.get("prior_results") or {}
        sql_rows: list | None = None
        columns: list = []
        for val in prior.values():
            if isinstance(val, dict) and "rows" in val and "columns" in val:
                sql_rows = val["rows"]
                columns = val["columns"]
                break

        if sql_rows is not None:
            filter_col = args.get("filter_column")
            filter_val = args.get("filter_value")
            apply_filter = filter_col and filter_val and filter_col in columns

            last_id = "unknown"
            flagged_count = 0
            for row in sql_rows:
                row_dict = dict(zip(columns, row)) if columns else {}
                if apply_filter and str(row_dict.get(filter_col)) != str(filter_val):
                    continue
                item_id = str(
                    row_dict.get("item_id")
                    or row_dict.get("entity")
                    or row_dict.get("partner_name")
                    or row_dict.get("partner")
                    or row_dict.get("name")
                    or "unknown"
                )
                self._flags.append({"item_id": item_id, "reason": reason, "severity": severity})
                last_id = item_id
                flagged_count += 1
            return {"flagged": flagged_count > 0, "item_id": last_id, "total_flagged": flagged_count}

        # Fallback: no explicit id and no SQL rows
        self._flags.append({"item_id": "unknown", "reason": reason, "severity": severity})
        return {"flagged": True, "item_id": "unknown", "total_flagged": 1}

    def reset(self) -> None:
        self._flags = []

    @property
    def flags(self) -> list[dict]:
        return list(self._flags)
