# aether/tools/run_sql.py

import re
import duckdb

from aether.tools.base import BaseTool
from aether.tools.load_data import LoadDataTool


class RunSQLTool(BaseTool):
    name = "run_sql"

    def run(self, args: dict) -> dict:
        sql = args["sql"]
        conn = duckdb.connect()

        for table_name, df in LoadDataTool._registry.items():
            conn.register(table_name, df)

        try:
            result = conn.execute(sql)
        except Exception as original_exc:
            rewritten = _try_cte_rewrite(sql)
            if rewritten is None:
                conn.close()
                raise
            try:
                result = conn.execute(rewritten)
            except Exception:
                conn.close()
                raise original_exc

        columns = [desc[0] for desc in result.description]
        rows = result.fetchall()
        conn.close()

        return {
            "columns": columns,
            "rows": [list(r) for r in rows],
            "row_count": len(rows),
        }


def _try_cte_rewrite(sql: str) -> str | None:
    """Best-effort: split SQL at the last WHERE clause and wrap as a CTE.

    Only attempted when the query contains both a window function and a WHERE
    clause — the most common DuckDB planning error from the planner.
    """
    upper = sql.upper()
    if "OVER" not in upper or "WHERE" not in upper:
        return None

    # Find the last WHERE not inside parentheses (top-level WHERE)
    depth = 0
    where_pos = None
    i = 0
    while i < len(sql):
        ch = sql[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0 and sql[i:i+5].upper() == "WHERE":
            where_pos = i
        i += 1

    if where_pos is None:
        return None

    inner = sql[:where_pos].rstrip()
    condition = sql[where_pos + 5:].strip()  # everything after WHERE

    return f"WITH cte AS ({inner}) SELECT * FROM cte WHERE {condition}"
