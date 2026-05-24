# aether/ingestion/table_parser.py
#
# Parse FinQA-style markdown tables (pandas .to_markdown() output) into
# clean DataFrames that load_data / run_sql can work with.
#
# Real table shapes observed in T2-RAGBench FinQA:
#   - Col 0 is always a pandas integer row index  → dropped
#   - Negative values encoded as "-84 ( 84 )"     → parsed as -84.0
#   - Currency prefix "$ 5735"                     → 5735.0
#   - Missing / zero cells encoded as "-"          → NaN
#   - Commas in large numbers                      → stripped
#   - Separator row (|---:|:---|) skipped
#   - Year sentinels in data cells (e.g. 2014 in
#     a row meaning N/A)                           → parsed as float as-is

from __future__ import annotations

import re
from io import StringIO
from typing import Optional

import pandas as pd


# ── cell-value cleaner ────────────────────────────────────────────────────────

_PAREN_NEG = re.compile(r"^(-?[\d,]+(?:\.\d+)?)\s*\(\s*[\d,]+(?:\.\d+)?\s*\)$")


def _parse_cell(raw: str) -> float | str | None:
    """
    Convert a raw markdown cell string to Python float, str, or None.

    Rules (applied in order):
      1. Empty / dash / "n/a"          → None
      2. Parenthetical negative:
         "-84 ( 84 )"  → -84.0
         Numbers without leading sign:
         "84 ( 84 )"   → -84.0   (accounting convention: parens = negative)
      3. Strip leading "$", commas, whitespace → float if possible
      4. Otherwise return as stripped string
    """
    v = raw.strip()
    if v in ("", "-", "—", "n/a", "na", "N/A"):
        return None

    # Parenthetical negative: "X ( Y )" or "-X ( Y )"
    m = _PAREN_NEG.match(v)
    if m:
        leading = m.group(1).replace(",", "")
        num = float(leading)
        # If the raw string has a leading minus, sign is already correct.
        # If there is NO leading minus, accounting convention → negative.
        if not v.startswith("-"):
            num = -abs(num)
        return num

    # Strip dollar sign and commas
    cleaned = v.replace("$", "").replace(",", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return v  # keep as string (e.g. row-label column)


# ── markdown → DataFrame ──────────────────────────────────────────────────────

_SEP_ROW = re.compile(r"^\|[\s\-:|]+\|$")


def parse_finqa_markdown_table(md: str) -> Optional[pd.DataFrame]:
    """
    Parse a T2-RAGBench / FinQA markdown table into a clean DataFrame.

    Returns None if the string cannot be parsed as a valid markdown table.

    Contract:
      - The leading integer index column is dropped.
      - Numeric cells are Python float; label cells remain str.
      - Missing cells are NaN (via None → pd.NA after astype).
      - Column names are stripped of leading/trailing whitespace but
        otherwise preserved so the agent can reference them verbatim.
    """
    if not md or not md.strip():
        return None

    lines = [ln.strip() for ln in md.strip().splitlines()]
    pipe_lines = [ln for ln in lines if ln.startswith("|")]
    if not pipe_lines:
        return None

    header_cells: list[str] | None = None
    data_rows: list[list] = []

    for ln in pipe_lines:
        if _SEP_ROW.match(ln):
            continue  # skip |---:|:---| separator
        cells = [c.strip() for c in ln.split("|")[1:-1]]  # drop outer empty splits
        if header_cells is None:
            header_cells = cells
        else:
            data_rows.append(cells)

    if not header_cells or not data_rows:
        return None

    # Detect and drop integer index column (col 0 where all data values are digits)
    first_col_data = [row[0] for row in data_rows if row]
    is_index = all(v.strip().lstrip("-").isdigit() for v in first_col_data if v.strip())
    if is_index:
        header_cells = header_cells[1:]
        data_rows = [row[1:] for row in data_rows]

    # Align row lengths (pad short rows with empty string)
    ncols = len(header_cells)
    data_rows = [(row + [""] * ncols)[:ncols] for row in data_rows]

    # Build raw DataFrame
    df = pd.DataFrame(data_rows, columns=header_cells)

    # Parse every column except the first (label / description column)
    label_col = df.columns[0]
    for col in df.columns[1:]:
        df[col] = df[col].apply(lambda x: _parse_cell(x) if isinstance(x, str) else x)

    # Clean label column: strip whitespace only
    df[label_col] = df[label_col].str.strip()

    return df


def table_to_csv(md: str, out_path: str) -> bool:
    """
    Parse markdown table and write to CSV. Returns True on success.
    """
    df = parse_finqa_markdown_table(md)
    if df is None:
        return False
    df.to_csv(out_path, index=False)
    return True
