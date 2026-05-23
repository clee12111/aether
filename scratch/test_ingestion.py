# test_ingestion.py
"""
Smoke test for the Week 1 ingestion + RAG stack.

Run from the project root:
    python test_ingestion.py

What it does:
  1. Looks for a sample file in data/demo/ (CSV, PDF, or Excel).
     If none exists, creates a small demo CSV automatically.
  2. Loads the file through DocumentLoader → list[Chunk].
  3. Indexes the chunks into Chroma via HybridRetriever.
  4. Runs one retrieve() call and prints the top results.
"""

from __future__ import annotations

import csv
import logging
import sys
import textwrap
from pathlib import Path

# ── Logging setup ──────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("test_ingestion")

# ── Paths ──────────────────────────────────────────────────────────────────────
DEMO_DIR = Path("data/demo")
DEMO_CSV = DEMO_DIR / "demo_fund.csv"

SUPPORTED_EXTS = {".csv", ".pdf", ".xlsx", ".xls", ".xlsm"}

DEMO_QUERY = "capital account allocation Q4"


# ── Demo data ──────────────────────────────────────────────────────────────────

DEMO_ROWS = [
    ["investor_id", "investor_name", "capital_commitment", "q4_allocation_pct", "q4_distribution", "notes"],
    ["INV001", "Sequoia Capital LP", "5000000", "12.5", "625000", "Allocation consistent with agreement"],
    ["INV002", "Tiger Global Fund", "3000000", "7.5", "225000", "Possible over-allocation — review required"],
    ["INV003", "Andreessen Horowitz", "8000000", "20.0", "1600000", "Consistent with fund agreement Section 4.2"],
    ["INV004", "Benchmark Capital", "2500000", "6.25", "156250", ""],
    ["INV005", "Founders Fund LP", "4000000", "10.0", "400000", "Q4 reconciliation complete"],
    ["INV006", "Lightspeed Ventures", "6000000", "15.0", "900000", "Allocation flag: exceeds 15% cap per agreement"],
    ["INV007", "General Catalyst", "3500000", "8.75", "306250", ""],
    ["INV008", "Accel Partners", "4500000", "11.25", "506250", "Consistent"],
    ["INV009", "Index Ventures", "2000000", "5.0", "100000", "Missing Q3 carry-forward documentation"],
    ["INV010", "Khosla Ventures", "3000000", "7.5", "225000", "Allocation consistent with agreement"],
    ["INV011", "NEA Fund XII", "1500000", "3.75", "56250", "Below minimum commitment threshold — flag for GP review"],
    ["INV012", "Greylock Partners", "5500000", "13.75", "756250", "Allocation consistent with agreement"],
]


def _ensure_demo_file() -> Path:
    """Return an existing demo file, or create a CSV if none found."""
    DEMO_DIR.mkdir(parents=True, exist_ok=True)

    # Look for any supported file already in data/demo/
    for ext in SUPPORTED_EXTS:
        candidates = list(DEMO_DIR.glob(f"*{ext}"))
        if candidates:
            path = candidates[0]
            logger.info("Found existing demo file: %s", path)
            return path

    # Nothing found — write a demo CSV
    logger.info("No demo file found — creating %s", DEMO_CSV)
    with open(DEMO_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerows(DEMO_ROWS)
    logger.info("Demo CSV written: %d data rows", len(DEMO_ROWS) - 1)
    return DEMO_CSV


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    # ── 1. Resolve demo file ───────────────────────────────────────────────────
    demo_path = _ensure_demo_file()
    print(f"\n{'=' * 60}")
    print(f"  Aether — Week 1 ingestion smoke test")
    print(f"{'=' * 60}")
    print(f"  File   : {demo_path}")
    print(f"  Query  : {DEMO_QUERY!r}")
    print(f"{'=' * 60}\n")

    # ── 2. Load chunks ─────────────────────────────────────────────────────────
    from aether.ingestion.loader import DocumentLoader

    loader = DocumentLoader()
    logger.info("Loading %s …", demo_path.name)
    chunks = loader.load(demo_path)

    print(f"[loader]  Produced {len(chunks)} chunk(s)")
    for i, chunk in enumerate(chunks[:3]):
        preview = chunk.content[:120].replace("\n", " ")
        print(f"  chunk[{i}]  id={chunk.short_id}  tokens={chunk.token_count}  {preview!r}")
    if len(chunks) > 3:
        print(f"  … and {len(chunks) - 3} more chunk(s)")
    print()

    # ── 3. Index into Chroma ───────────────────────────────────────────────────
    from aether.rag.retriever import HybridRetriever

    retriever = HybridRetriever()
    logger.info("Indexing %d chunks into Chroma …", len(chunks))
    retriever.index(chunks)
    print(f"[retriever]  Indexed {len(chunks)} chunk(s) into Chroma\n")

    # ── 4. Retrieve ────────────────────────────────────────────────────────────
    logger.info("Running retrieval: %r", DEMO_QUERY)
    results = retriever.retrieve(DEMO_QUERY, top_k=3)

    print(f"[retriever]  Top {len(results)} result(s) for query: {DEMO_QUERY!r}\n")
    if not results:
        print("  (no results returned — check that the collection was indexed correctly)")
        sys.exit(1)

    for rank, chunk in enumerate(results, start=1):
        source = chunk.metadata.source_path.split("/")[-1].split("\\")[-1]
        location = _chunk_location(chunk)
        print(f"  Rank {rank}  [{chunk.short_id}]  {source}  {location}")
        wrapped = textwrap.fill(
            chunk.content[:300].replace("\n", " "),
            width=72,
            initial_indent="    ",
            subsequent_indent="    ",
        )
        print(wrapped)
        print()

    print("Smoke test passed.")


def _chunk_location(chunk) -> str:  # type: ignore[no-untyped-def]
    """Return a short human-readable location string for a chunk."""
    m = chunk.metadata
    if m.page_number is not None:
        return f"page {m.page_number}"
    if m.row_start is not None:
        label = f"sheet '{m.sheet_name}' | " if m.sheet_name else ""
        return f"{label}rows {m.row_start}–{m.row_end}"
    return ""


if __name__ == "__main__":
    main()
