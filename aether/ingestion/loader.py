# aether/ingestion/loader.py

"""
DocumentLoader — parses CSV, PDF, Excel, and plain text files into Chunk objects.

Supported formats:
  .csv              → row-group chunks; column headers repeated in every chunk
  .pdf              → page-level chunks; long pages split by character window
  .xlsx / .xls      → per-sheet row-group chunks (same strategy as CSV)
  .txt              → character-window chunks with overlap

Usage::

    from aether.ingestion.loader import DocumentLoader

    loader = DocumentLoader()
    chunks = loader.load("data/demo/fund_agreement.pdf")
    # → list[Chunk]
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

import pandas as pd
import pdfplumber

from aether.config import settings
from aether.models.chunk import Chunk, ChunkMetadata

logger = logging.getLogger(__name__)

_CSV_EXTS: frozenset[str] = frozenset({".csv"})
_PDF_EXTS: frozenset[str] = frozenset({".pdf"})
_EXCEL_EXTS: frozenset[str] = frozenset({".xlsx", ".xls", ".xlsm"})
_TEXT_EXTS: frozenset[str] = frozenset({".txt"})
_ALL_EXTS: frozenset[str] = _CSV_EXTS | _PDF_EXTS | _EXCEL_EXTS | _TEXT_EXTS

# Encodings tried in order for CSV files
_CSV_ENCODINGS: tuple[str, ...] = ("utf-8", "utf-8-sig", "latin-1", "cp1252")


class DocumentLoader:
    """Converts raw financial documents into ``Chunk`` lists.

    Args:
        chunk_size:     Maximum character length of a single text chunk.
                        Applies to PDF / text splitting only.
        chunk_overlap:  Character overlap between consecutive text chunks.
        rows_per_chunk: Number of data rows per chunk for CSV / Excel.
    """

    def __init__(
        self,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
        rows_per_chunk: int | None = None,
    ) -> None:
        self.chunk_size = chunk_size if chunk_size is not None else settings.chunk_size
        self.chunk_overlap = chunk_overlap if chunk_overlap is not None else settings.chunk_overlap
        self.rows_per_chunk = rows_per_chunk if rows_per_chunk is not None else settings.rows_per_chunk

        if self.chunk_overlap >= self.chunk_size:
            raise ValueError(
                f"chunk_overlap ({self.chunk_overlap}) must be < chunk_size ({self.chunk_size})"
            )

    # ── Public API ─────────────────────────────────────────────────────────────

    def load(self, path: str | Path) -> list[Chunk]:
        """Parse a document and return its chunks.

        Args:
            path: Path to a .csv, .pdf, .xlsx, or .xls file.

        Returns:
            Non-empty list of Chunk objects with token_count set.

        Raises:
            FileNotFoundError: The path does not exist.
            ValueError:        Unsupported extension, or document has no content.
        """
        p = Path(path).resolve()
        if not p.exists():
            raise FileNotFoundError(f"Document not found: {p}")
        if not p.is_file():
            raise ValueError(f"Path is not a file: {p}")

        ext = p.suffix.lower()
        if ext not in _ALL_EXTS:
            raise ValueError(
                f"Unsupported file type {ext!r}. "
                f"Supported: {sorted(_ALL_EXTS)}"
            )

        doc_id = _document_id(p)
        logger.info("Loading %s (doc_id=%s…)", p.name, doc_id[:12])

        if ext in _CSV_EXTS:
            chunks = self._load_csv(p, doc_id)
        elif ext in _PDF_EXTS:
            chunks = self._load_pdf(p, doc_id)
        elif ext in _TEXT_EXTS:
            chunks = self._load_text(p, doc_id)
        else:
            chunks = self._load_excel(p, doc_id)

        for chunk in chunks:
            chunk.token_count = len(chunk.content.split())

        logger.info("Produced %d chunks from %s", len(chunks), p.name)
        return chunks

    def load_many(self, paths: list[str | Path]) -> list[Chunk]:
        """Load multiple documents; raises on the first failure."""
        all_chunks: list[Chunk] = []
        for path in paths:
            all_chunks.extend(self.load(path))
        return all_chunks

    # ── CSV ────────────────────────────────────────────────────────────────────

    def _load_csv(self, path: Path, doc_id: str) -> list[Chunk]:
        df = _read_csv_safe(path)

        if df.empty:
            raise ValueError(f"CSV file has no data rows: {path}")

        df.columns = [str(c).strip() for c in df.columns]
        col_names = list(df.columns)
        total_rows = len(df)
        chunks: list[Chunk] = []

        for start in range(0, total_rows, self.rows_per_chunk):
            end = min(start + self.rows_per_chunk - 1, total_rows - 1)
            slice_df = df.iloc[start : end + 1].dropna(how="all")
            if slice_df.empty:
                continue

            rows_text = slice_df.to_csv(index=False, sep="|").strip()
            content = f"[CSV: {path.name} | rows {start}–{end}]\n{rows_text}"

            chunks.append(
                Chunk(
                    document_id=doc_id,
                    content=content,
                    metadata=ChunkMetadata(
                        source_path=str(path),
                        document_type="csv",
                        row_start=start,
                        row_end=end,
                        column_names=col_names,
                        extra={"total_rows": total_rows},
                    ),
                )
            )

        if not chunks:
            raise ValueError(f"CSV produced no non-empty chunks: {path}")
        return chunks

    # ── PDF ────────────────────────────────────────────────────────────────────

    def _load_pdf(self, path: Path, doc_id: str) -> list[Chunk]:
        chunks: list[Chunk] = []

        with pdfplumber.open(str(path)) as pdf:
            total_pages = len(pdf.pages)

            if total_pages == 0:
                raise ValueError(f"PDF has no pages: {path}")

            for page_idx, page in enumerate(pdf.pages):
                page_num = page_idx + 1
                text = _normalise_whitespace(page.extract_text() or "")

                if not text:
                    logger.debug("Skipping empty page %d/%d in %s", page_num, total_pages, path.name)
                    continue

                if len(text) <= self.chunk_size:
                    chunks.append(
                        Chunk(
                            document_id=doc_id,
                            content=f"[PDF: {path.name} | page {page_num}/{total_pages}]\n{text}",
                            metadata=ChunkMetadata(
                                source_path=str(path),
                                document_type="pdf",
                                page_number=page_num,
                                char_start=0,
                                char_end=len(text),
                                extra={"total_pages": total_pages},
                            ),
                        )
                    )
                else:
                    windows = self._split_text(text)
                    for i, window in enumerate(windows):
                        char_start = max(0, i * (self.chunk_size - self.chunk_overlap))
                        chunks.append(
                            Chunk(
                                document_id=doc_id,
                                content=(
                                    f"[PDF: {path.name} | page {page_num}/{total_pages}"
                                    f" | part {i + 1}/{len(windows)}]\n{window}"
                                ),
                                metadata=ChunkMetadata(
                                    source_path=str(path),
                                    document_type="pdf",
                                    page_number=page_num,
                                    char_start=char_start,
                                    char_end=char_start + len(window),
                                    extra={"total_pages": total_pages, "part_index": i},
                                ),
                            )
                        )

        if not chunks:
            raise ValueError(
                f"No extractable text found in PDF: {path}. "
                "The file may be scanned — OCR is not supported in this version."
            )
        return chunks

    # ── Plain text ─────────────────────────────────────────────────────────────

    def _load_text(self, path: Path, doc_id: str) -> list[Chunk]:
        text = _normalise_whitespace(path.read_text(encoding="utf-8"))
        if not text:
            raise ValueError(f"Text file is empty: {path}")

        windows = self._split_text(text)
        chunks: list[Chunk] = []
        for i, window in enumerate(windows):
            char_start = max(0, i * (self.chunk_size - self.chunk_overlap))
            chunks.append(
                Chunk(
                    document_id=doc_id,
                    content=f"[TXT: {path.name} | part {i + 1}/{len(windows)}]\n{window}",
                    metadata=ChunkMetadata(
                        source_path=str(path),
                        document_type="text",
                        char_start=char_start,
                        char_end=char_start + len(window),
                        extra={"total_parts": len(windows)},
                    ),
                )
            )
        return chunks

    # ── Excel ──────────────────────────────────────────────────────────────────

    def _load_excel(self, path: Path, doc_id: str) -> list[Chunk]:
        xl = pd.ExcelFile(str(path))
        chunks: list[Chunk] = []

        for sheet_name in xl.sheet_names:
            try:
                df = xl.parse(sheet_name, dtype=str, keep_default_na=False)
            except Exception as exc:
                logger.warning("Skipping sheet %r in %s: %s", sheet_name, path.name, exc)
                continue

            df.columns = [str(c).strip() for c in df.columns]
            df = df.dropna(how="all").reset_index(drop=True)

            if df.empty:
                logger.debug("Skipping empty sheet %r in %s", sheet_name, path.name)
                continue

            col_names = list(df.columns)
            total_rows = len(df)

            for start in range(0, total_rows, self.rows_per_chunk):
                end = min(start + self.rows_per_chunk - 1, total_rows - 1)
                slice_df = df.iloc[start : end + 1]
                rows_text = slice_df.to_csv(index=False, sep="|").strip()
                content = (
                    f"[Excel: {path.name} | sheet '{sheet_name}' | rows {start}–{end}]\n"
                    f"{rows_text}"
                )
                chunks.append(
                    Chunk(
                        document_id=doc_id,
                        content=content,
                        metadata=ChunkMetadata(
                            source_path=str(path),
                            document_type="excel",
                            row_start=start,
                            row_end=end,
                            sheet_name=str(sheet_name),
                            column_names=col_names,
                            extra={"total_rows": total_rows},
                        ),
                    )
                )

        if not chunks:
            raise ValueError(f"No extractable data found in Excel file: {path}")
        return chunks

    # ── Text splitting ─────────────────────────────────────────────────────────

    def _split_text(self, text: str) -> list[str]:
        """Split text into overlapping character windows with clean break points."""
        if len(text) <= self.chunk_size:
            return [text]

        windows: list[str] = []
        start = 0

        while start < len(text):
            end = min(start + self.chunk_size, len(text))

            if end < len(text):
                look_back_start = max(start, end - self.chunk_size // 5)
                best = _find_break(text, look_back_start, end)
                if best > start:
                    end = best

            window = text[start:end].strip()
            if window:
                windows.append(window)

            if end >= len(text):
                break
            start = end - self.chunk_overlap

        return windows


# ── Module helpers ─────────────────────────────────────────────────────────────

def _document_id(path: Path) -> str:
    """Stable fingerprint: SHA-256(absolute_path + file_size)."""
    stat = path.stat()
    return hashlib.sha256(f"{path}:{stat.st_size}".encode()).hexdigest()


def _read_csv_safe(path: Path) -> pd.DataFrame:
    """Read a CSV trying multiple encodings before raising."""
    last_exc: Exception | None = None
    for enc in _CSV_ENCODINGS:
        try:
            return pd.read_csv(path, dtype=str, keep_default_na=False, encoding=enc)
        except UnicodeDecodeError as exc:
            last_exc = exc
        except Exception:
            raise
    raise ValueError(
        f"Could not decode {path.name} with any of {_CSV_ENCODINGS}"
    ) from last_exc


def _normalise_whitespace(text: str) -> str:
    """Collapse horizontal whitespace runs; reduce 3+ newlines to 2."""
    text = re.sub(r"[^\S\n]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _find_break(text: str, start: int, end: int) -> int:
    """Return the position of the last clean break in ``text[start:end]``."""
    for sep in ("\n\n", "\n", ". ", "? ", "! "):
        idx = text.rfind(sep, start, end)
        if idx != -1:
            return idx + len(sep)
    return end
