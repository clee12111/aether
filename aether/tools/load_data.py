# aether/tools/load_data.py

import logging
import re
from pathlib import Path

import pandas as pd

from aether.config import settings
from aether.tools.base import BaseTool

logger = logging.getLogger(__name__)

# ── Financial-number coercion ─────────────────────────────────────────────────

_DASH_ZERO_RE = re.compile(r"^\$?\s*-\s*$")
_PAREN_NEG_RE = re.compile(r"^\((.+)\)$")


def _coerce_financial_value(val: str) -> float | None:
    """Coerce a single string value using accounting conventions.

    Order of operations:
      1. Strip whitespace
      2. Strip surrounding quotes
      3. Dash-zeros ($-, -, empty) → None (null — honest for missing)
      4. Remove $ and currency symbols
      5. Parenthesized negatives: (1,234.50) → -1234.50
      6. Remove ALL commas (handles irregular grouping like 5,29,550)
      7. float() parse
    """
    if not isinstance(val, str):
        return None
    s = val.strip()
    # Strip surrounding quotes
    if len(s) >= 2 and s[0] in ('"', "'") and s[-1] == s[0]:
        s = s[1:-1].strip()
    # Empty or dash-zero
    if not s or _DASH_ZERO_RE.match(s):
        return None
    # Remove currency symbols
    s = s.replace("$", "").replace("£", "").replace("€", "").replace("¥", "").strip()
    # Parenthesized negatives
    negate = False
    m = _PAREN_NEG_RE.match(s)
    if m:
        s = m.group(1).strip()
        negate = True
    # Remove all commas
    s = s.replace(",", "")
    # Trailing % — keep as-is, let float parse fail on non-numeric remainder
    try:
        result = float(s)
        return -result if negate else result
    except ValueError:
        return None


def _coerce_financial_column(series: pd.Series, threshold: float = 0.8) -> pd.Series:
    """Attempt to coerce a string column to numeric using accounting conventions.

    Converts only if >= threshold fraction of non-empty values successfully parse.
    Returns the original series unchanged if the column is genuinely categorical.
    """
    if not pd.api.types.is_string_dtype(series):
        return series

    non_empty = series.dropna().astype(str).str.strip()
    non_empty = non_empty[non_empty != ""]
    if len(non_empty) == 0:
        return series

    coerced = non_empty.apply(_coerce_financial_value)
    success_count = coerced.notna().sum()
    success_rate = success_count / len(non_empty)

    if success_rate < threshold:
        return series

    # Apply to the full series (including NaN positions)
    return series.astype(str).apply(
        lambda v: _coerce_financial_value(v) if isinstance(v, str) and str(v).strip() else None
    ).astype("Float64")


def _clean_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Clean a DataFrame for SQL use: strip headers, coerce financial numbers."""
    # Strip whitespace from column headers
    df.columns = [str(c).strip() for c in df.columns]
    # Coerce each column
    for col in df.columns:
        df[col] = _coerce_financial_column(df[col])
    return df


class LoadDataTool(BaseTool):
    name = "load_data"

    # Shared registry so RunSQLTool can access loaded frames
    _registry: dict[str, pd.DataFrame] = {}

    def reset(self) -> None:
        LoadDataTool._registry.clear()

    def run(self, args: dict) -> dict:
        file_path = _resolve_path(args["file_path"])
        table_name = args["table_name"]
        ext = file_path.suffix.lower()

        if ext in {".csv"}:
            df = pd.read_csv(file_path)
        elif ext in {".xlsx", ".xls"}:
            df = pd.read_excel(file_path)
        elif ext in {".pdf"}:
            return _load_pdf_table(file_path, table_name)
        else:
            return {
                "error": (
                    f"Unsupported file type '{ext}' for load_data. "
                    "Supported: .csv, .xlsx, .xls, .pdf. "
                    "For text files, use retrieve_context instead."
                ),
            }

        df = _clean_dataframe(df)
        LoadDataTool._registry[table_name] = df
        return {
            "table_name": table_name,
            "row_count": len(df),
            "columns": df.columns.tolist(),
        }


def _is_separator_col(series: pd.Series) -> bool:
    """True if a column contains only '$', empty strings, or whitespace."""
    return series.astype(str).str.strip().str.replace("$", "", regex=False).eq("").all()


def _clean_pdf_df(df: pd.DataFrame) -> pd.DataFrame:
    """Clean a raw Camelot-extracted DataFrame for SQL use.

    1. Drop dollar-sign / whitespace-only separator columns.
    2. Strip whitespace from all cells.
    3. Coerce numeric columns using the shared financial-number helper.
    """
    # Drop separator columns
    keep = [c for c in df.columns if not _is_separator_col(df[c])]
    df = df[keep].copy()

    # Strip whitespace from cells
    for col in df.columns:
        df[col] = df[col].astype(str).str.strip()

    # Coerce using the shared financial-number helper
    for col in df.columns:
        df[col] = _coerce_financial_column(df[col])

    return df


def _load_pdf_table(file_path: Path, table_name: str) -> dict:
    """Extract tables from a PDF using Camelot stream mode.

    Stream mode handles whitespace-aligned tables (no ruling lines) that
    pdfplumber.extract_tables() misses — verified on financial statements.

    Registers ALL extracted tables so run_sql can target the right one:
      <table_name>_0, <table_name>_1, ...
    Also registers the largest as <table_name> for convenience.

    Returns a normal tool observation dict (never raises).
    """
    import camelot

    try:
        tables = camelot.read_pdf(str(file_path), pages="all", flavor="stream")
    except Exception as exc:
        logger.warning("Camelot failed on %s: %s", file_path.name, exc)
        return {
            "error": (
                f"Could not extract tables from PDF '{file_path.name}': {exc}. "
                "Use retrieve_context + answer_from_context for text-based questions."
            ),
        }

    if len(tables) == 0:
        return {
            "error": (
                f"No tables found in PDF '{file_path.name}'. "
                "This PDF may contain only narrative text. "
                "Use retrieve_context + answer_from_context instead of load_data."
            ),
        }

    # Clean and register each table
    registered: list[dict] = []
    largest_name: str | None = None
    largest_rows = 0

    for i, t in enumerate(tables):
        df = _clean_pdf_df(t.df)

        # Skip trivially small tables (< 2 rows after cleanup)
        if len(df) < 2:
            continue

        # Use first row as header if it looks like labels (mostly non-numeric)
        first_row = df.iloc[0]
        numeric_count = sum(
            1 for v in first_row
            if str(v).replace(",", "").replace(".", "").replace("-", "").isdigit()
            and str(v).strip()
        )
        if numeric_count <= len(first_row) // 2:
            # First row is labels — promote to header
            raw_headers = [
                str(v).strip() if str(v).strip() else f"col_{ci}"
                for ci, v in enumerate(first_row)
            ]
            # Deduplicate: append _2, _3, ... for repeats
            seen: dict[str, int] = {}
            headers: list[str] = []
            for h in raw_headers:
                if h in seen:
                    seen[h] += 1
                    headers.append(f"{h}_{seen[h]}")
                else:
                    seen[h] = 1
                    headers.append(h)
            df = df.iloc[1:].reset_index(drop=True)
            df.columns = headers
        else:
            df.columns = [f"col_{ci}" for ci in range(len(df.columns))]

        # Re-coerce after header promotion using the shared financial-number helper
        for col in df.columns:
            if pd.api.types.is_string_dtype(df[col]):
                df[col] = _coerce_financial_column(df[col])

        tname = f"{table_name}_{i}"
        LoadDataTool._registry[tname] = df
        registered.append({
            "table_name": tname,
            "page": t.page,
            "row_count": len(df),
            "columns": df.columns.tolist(),
        })
        if len(df) > largest_rows:
            largest_rows = len(df)
            largest_name = tname

        logger.info(
            "Registered PDF table %s (page %s): %d rows x %d cols",
            tname, t.page, len(df), len(df.columns),
        )

    if not registered:
        return {
            "error": (
                f"PDF '{file_path.name}' contained tables but none had enough "
                "data rows after cleanup. "
                "Use retrieve_context + answer_from_context instead."
            ),
        }

    # Also register the largest table under the bare table_name for convenience
    if largest_name:
        LoadDataTool._registry[table_name] = LoadDataTool._registry[largest_name]

    logger.info(
        "Extracted %d tables from PDF %s, largest=%s (%d rows)",
        len(registered), file_path.name, largest_name, largest_rows,
    )
    return {
        "table_name": table_name,
        "tables_registered": registered,
        "largest": largest_name,
        "total_tables": len(registered),
        "source": f"{len(registered)} table(s) from {file_path.name} via Camelot stream",
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
