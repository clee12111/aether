# aether/models/chunk.py

"""
Chunk — the atomic unit flowing from ingestion through RAG into agent prompts.

Every chunk carries full provenance so the Critic can cite exact sources
(file, page, row range) without re-reading the originals.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


DocumentType = Literal["csv", "pdf", "excel", "text"]


class ChunkMetadata(BaseModel):
    """Provenance and structural metadata for a single chunk.

    Attributes:
        source_path:   Absolute path to the originating file.
        document_type: Format the chunk was extracted from.
        page_number:   1-indexed PDF page (PDF only).
        row_start:     First data row, 0-indexed (CSV / Excel).
        row_end:       Last data row, 0-indexed inclusive (CSV / Excel).
        sheet_name:    Worksheet name (Excel only).
        column_names:  Column headers in this chunk (CSV / Excel).
        char_start:    Character offset of chunk start within page text (PDF).
        char_end:      Character offset of chunk end within page text (PDF).
        extra:         Loader-specific overflow metadata.
    """

    source_path: str
    document_type: DocumentType

    # PDF
    page_number: int | None = Field(default=None, ge=1)

    # Tabular
    row_start: int | None = Field(default=None, ge=0)
    row_end: int | None = Field(default=None, ge=0)
    sheet_name: str | None = None
    column_names: list[str] | None = None

    # Text position
    char_start: int | None = Field(default=None, ge=0)
    char_end: int | None = Field(default=None, ge=0)

    extra: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def row_range_consistent(self) -> ChunkMetadata:
        if self.row_start is not None and self.row_end is not None:
            if self.row_end < self.row_start:
                raise ValueError(
                    f"row_end ({self.row_end}) must be >= row_start ({self.row_start})"
                )
        return self


class Chunk(BaseModel):
    """An extracted, optionally-embedded slice of a source document.

    Produced by ``DocumentLoader``; ``embedding`` is populated by
    ``HybridRetriever.index()`` before the chunk is stored in Chroma.

    Attributes:
        chunk_id:     UUID4 primary key.
        document_id:  SHA-256 fingerprint of the source file (path + size).
        content:      Raw text including the header line added by the loader
                      (e.g. ``[CSV: fund.csv | rows 0–49]``).
        metadata:     Full provenance.
        embedding:    Dense vector; None until indexed.
        token_count:  Approximate whitespace-token count; set by loader.
    """

    chunk_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    document_id: str
    content: str = Field(..., min_length=1)
    metadata: ChunkMetadata
    embedding: list[float] | None = None
    token_count: int | None = Field(default=None, ge=0)

    @field_validator("content")
    @classmethod
    def content_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Chunk content must contain non-whitespace characters")
        return v

    # ── Convenience ───────────────────────────────────────────────────────────

    def bm25_tokens(self) -> list[str]:
        """Lowercase whitespace-split tokens for BM25 indexing."""
        return self.content.lower().split()

    @property
    def short_id(self) -> str:
        """First 8 chars of chunk_id for log / display purposes."""
        return self.chunk_id[:8]

    def to_chroma_metadata(self) -> dict[str, str | int | float | bool]:
        """Serialise to a flat Chroma-compatible metadata dict.

        Chroma only accepts str / int / float / bool values, so nested
        structures are stored as a JSON string under ``metadata_json``.
        Top-level scalar fields are kept flat for ``where``-clause filtering.
        """
        return {
            "chunk_id": self.chunk_id,
            "document_id": self.document_id,
            "token_count": self.token_count or 0,
            "source_path": self.metadata.source_path,
            "document_type": self.metadata.document_type,
            "metadata_json": self.metadata.model_dump_json(),
        }

    @classmethod
    def from_chroma_record(
        cls,
        *,
        chunk_id: str,
        document: str,
        chroma_meta: dict[str, Any],
    ) -> Chunk:
        """Reconstruct a Chunk from a Chroma query / get result row.

        Args:
            chunk_id:    The Chroma point ID (equals chunk_id).
            document:    The raw document text stored in Chroma.
            chroma_meta: The flat metadata dict returned by Chroma.

        Raises:
            KeyError:   If a required key is missing.
            ValueError: If ``metadata_json`` cannot be parsed.
        """
        meta_raw = json.loads(chroma_meta["metadata_json"])
        return cls(
            chunk_id=chunk_id,
            document_id=chroma_meta["document_id"],
            content=document,
            metadata=ChunkMetadata(**meta_raw),
            token_count=chroma_meta.get("token_count") or None,
        )
