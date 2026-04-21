# aether/tools/flag_item.py

from aether.tools.base import BaseTool

_VALID_SEVERITIES = {"low", "medium", "high"}


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

        # If prior_results contains SQL rows, flag each row individually
        prior = args.get("prior_results") or {}
        sql_rows: list | None = None
        for val in prior.values():
            if isinstance(val, dict) and "rows" in val and "columns" in val:
                sql_rows = val["rows"]
                columns = val["columns"]
                break

        if sql_rows is not None:
            last_id = "unknown"
            for row in sql_rows:
                row_dict = dict(zip(columns, row)) if columns else {}
                item_id = str(
                    row_dict.get("item_id")
                    or row_dict.get("partner_name")
                    or row_dict.get("partner")
                    or row_dict.get("name")
                    or "unknown"
                )
                self._flags.append({"item_id": item_id, "reason": reason, "severity": severity})
                last_id = item_id
            return {"flagged": True, "item_id": last_id, "total_flagged": len(sql_rows)}

        # Single-item flag from explicit args
        item_id = str(
            args.get("item_id")
            or args.get("partner")
            or args.get("name")
            or args.get("partner_name")
            or "unknown"
        )
        self._flags.append({"item_id": item_id, "reason": reason, "severity": severity})
        return {"flagged": True, "item_id": item_id, "total_flagged": 1}

    @property
    def flags(self) -> list[dict]:
        return list(self._flags)
