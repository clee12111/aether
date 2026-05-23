# aether/rag/retriever.py

"""
HybridRetriever — BM25 + dense vector search with cross-encoder reranking.
Uses ChromaDB as the persistent vector store (no Docker required).

Retrieval pipeline for a single query:
  1. Embed query with sentence-transformers (cosine-normalised)
  2. Dense ANN search in Chroma            → ``dense_top_k`` candidates
  3. BM25 search over in-memory index      → ``bm25_top_k`` candidates
  4. Merge via Reciprocal Rank Fusion
  5. Cross-encoder rerank with flashrank
  6. Return top ``rerank_top_k`` Chunks

Usage::

    from aether.rag.retriever import HybridRetriever
    from aether.ingestion.loader import DocumentLoader

    retriever = HybridRetriever()
    chunks = DocumentLoader().load("data/demo/fund_agreement.pdf")
    retriever.index(chunks)

    results = retriever.retrieve("Q4 capital account allocations", top_k=5)
"""

from __future__ import annotations

import logging
import re

import numpy as np
from rank_bm25 import BM25Okapi

from aether.config import settings
from aether.models.chunk import Chunk

logger = logging.getLogger(__name__)

# ── Query classification patterns ─────────────────────────────────────────────
# Generic patterns — no domain-specific terms.  Column-name matching is built
# dynamically from indexed chunk metadata (see _rebuild_column_pattern).

_DATA_NUMBERS = re.compile(r"\$[\d,]+|\b\d[\d,]*000\b|\b\d+%")
_DATA_AGGREGATES = re.compile(
    r"(?i)\b(highest|lowest|total|sum|how much|how many|which|who)\b"
)
_POLICY_KEYWORDS = re.compile(
    r"\b(rule|policy|section|limit|cap|requirement|allowed|threshold|compliance|regulation)\b"
    r"|days to review|must be retained|risk profile",
    re.IGNORECASE,
)

_EMBED_BATCH = 64    # sentence-transformers encode batch size
_UPSERT_BATCH = 256  # Chroma upsert batch size


class HybridRetriever:
    """Hybrid BM25 + dense retriever backed by ChromaDB.

    The BM25 index lives in process memory and is built (or rebuilt) each
    time ``index()`` is called.  Dense vectors persist in Chroma across
    process restarts; on cold start the BM25 index is automatically
    repopulated by reading the full Chroma collection.

    Args:
        collection_name: Chroma collection name.
                         Defaults to ``settings.chroma_collection``.
        dense_top_k:     ANN candidates fetched from Chroma per query.
        bm25_top_k:      BM25 candidates fetched from the in-memory index.
        rerank_top_k:    Final result count after cross-encoder reranking.
    """

    def __init__(
        self,
        collection_name: str | None = None,
        dense_top_k: int | None = None,
        bm25_top_k: int | None = None,
        rerank_top_k: int | None = None,
        ephemeral: bool = False,
    ) -> None:
        self.collection_name = collection_name or settings.chroma_collection
        self.dense_top_k = dense_top_k or settings.dense_top_k
        self.bm25_top_k = bm25_top_k or settings.bm25_top_k
        self.rerank_top_k = rerank_top_k or settings.rerank_top_k

        # Lazy-loaded on first use to avoid heavy startup cost on Windows
        logger.info("Loading encoder: %s", settings.embedding_model)
        from sentence_transformers import SentenceTransformer
        self._encoder = SentenceTransformer(settings.embedding_model)

        # flashrank is loaded lazily in _rerank()
        self._ranker = None

        import chromadb
        if ephemeral:
            # In-memory store — no disk I/O, no cross-session pollution.
            # Use for eval runs so dev scratch runs never pollute test retrieval.
            logger.info("Using ephemeral (in-memory) Chroma store")
            self._chroma = chromadb.EphemeralClient()
        else:
            logger.info("Opening Chroma store at: %s", settings.chroma_path)
            self._chroma = chromadb.PersistentClient(path=settings.chroma_path)
        self._collection = self._get_or_create_collection(self.collection_name)

        # In-memory BM25 state — built by index() or _warm_bm25()
        self._bm25: BM25Okapi | None = None
        self._bm25_chunks: list[Chunk] = []

        # Dynamic column-name pattern — rebuilt by index() / _warm_bm25()
        self._indexed_columns: set[str] = set()
        self._data_columns_re: re.Pattern[str] | None = None

    # ── Indexing ───────────────────────────────────────────────────────────────

    def index(self, chunks: list[Chunk], collection_name: str | None = None) -> None:
        """Embed and upsert chunks into Chroma, then rebuild the BM25 index.

        Chroma upsert semantics mean existing records with the same chunk_id
        are overwritten, so calling this multiple times is idempotent.

        Args:
            chunks:          Non-empty list from ``DocumentLoader.load()``.
            collection_name: Override the default collection.

        Raises:
            ValueError: If ``chunks`` is empty.
        """
        if not chunks:
            raise ValueError("index() requires a non-empty chunk list")

        col = self._resolve_collection(collection_name)

        logger.info("Embedding %d chunks …", len(chunks))
        vecs: np.ndarray = self._encoder.encode(
            [c.content for c in chunks],
            batch_size=_EMBED_BATCH,
            show_progress_bar=False,
            normalize_embeddings=True,
        )

        for chunk, vec in zip(chunks, vecs, strict=True):
            chunk.embedding = vec.tolist()

        logger.info("Upserting %d chunks into Chroma collection %r …", len(chunks), col.name)
        for i in range(0, len(chunks), _UPSERT_BATCH):
            batch = chunks[i : i + _UPSERT_BATCH]
            col.upsert(
                ids=[c.chunk_id for c in batch],
                embeddings=[c.embedding for c in batch],  # type: ignore[misc]
                documents=[c.content for c in batch],
                metadatas=[c.to_chroma_metadata() for c in batch],
            )

        # Merge into BM25 state, avoiding duplicates
        existing_ids = {c.chunk_id for c in self._bm25_chunks}
        self._bm25_chunks.extend(c for c in chunks if c.chunk_id not in existing_ids)
        self._rebuild_bm25()
        self._collect_columns(chunks)

        logger.info(
            "Index ready: %d total chunks, collection=%r",
            len(self._bm25_chunks),
            col.name,
        )

    def reset_index(self) -> None:
        """Clear all indexed state without unloading the encoder or reranker.

        Deletes the Chroma collection and recreates it empty, then zeros all
        in-memory BM25 state. Safe to call between eval cases to guarantee
        per-case corpus isolation — each case retrieves only its own documents.

        Works with both PersistentClient (clears disk) and EphemeralClient.
        """
        logger.info("reset_index: deleting collection %r", self.collection_name)
        try:
            self._chroma.delete_collection(self.collection_name)
        except Exception as exc:
            logger.warning("delete_collection(%r) failed (may not exist): %s",
                           self.collection_name, exc)

        self._collection = self._get_or_create_collection(self.collection_name)
        count = self._collection.count()
        if count != 0:
            raise RuntimeError(
                f"reset_index: collection still has {count} chunks after delete+recreate"
            )

        # Zero all in-memory BM25 and column state
        self._bm25_chunks = []
        self._bm25 = None
        self._indexed_columns = set()
        self._data_columns_re = None

        logger.info("reset_index: complete — collection %r is empty", self.collection_name)

    # ── Query classification ─────────────────────────────────────────────────

    def _classify_query(self, query: str) -> str:
        """Classify a query as ``"data"``, ``"policy"``, or ``"any"``.

        Used to apply metadata filtering before reranking so that CSV/Excel
        queries are not drowned by text/PDF chunks that share vocabulary.
        Column-name matching is built dynamically from indexed chunk metadata.
        """
        if (_DATA_NUMBERS.search(query)
                or (self._data_columns_re is not None and self._data_columns_re.search(query))
                or _DATA_AGGREGATES.search(query)):
            return "data"
        if _POLICY_KEYWORDS.search(query):
            return "policy"
        return "any"

    # ── Retrieval ──────────────────────────────────────────────────────────────

    def retrieve(
        self,
        query: str,
        top_k: int | None = None,
        collection_name: str | None = None,
    ) -> list[Chunk]:
        """Hybrid retrieval: dense ANN + BM25 → RRF merge → cross-encoder rerank.

        On cold start (empty BM25 index), chunks are read from Chroma and the
        BM25 index is rebuilt before searching.

        Args:
            query:           Natural-language query string.
            top_k:           Final result count (overrides ``rerank_top_k``).
            collection_name: Override the default collection.

        Returns:
            Ranked list of Chunk objects, best first.  Length ≤ ``top_k``.

        Raises:
            ValueError: If ``query`` is blank.
        """
        if not query.strip():
            raise ValueError("query must not be empty")

        col = self._resolve_collection(collection_name)
        final_k = top_k if top_k is not None else self.rerank_top_k

        if not self._bm25_chunks:
            logger.info("BM25 index empty — warming from Chroma collection %r …", col.name)
            self._warm_bm25(col)

        dense = self._dense_search(query, col, self.dense_top_k)
        bm25 = self._bm25_search(query, self.bm25_top_k)

        if not dense and not bm25:
            logger.warning("No candidates found for query: %r", query[:80])
            return []

        fused = _reciprocal_rank_fusion(
            [dense, bm25],
            top_k=max(self.dense_top_k, self.bm25_top_k),
        )

        # Query-aware metadata filtering before reranking
        intent = self._classify_query(query)
        if intent != "any":
            fused = self._filter_by_intent(fused, intent, final_k)
            logger.debug("Query classified as %r — %d chunks after filtering", intent, len(fused))

        result = self._rerank(query, fused, final_k)

        logger.debug(
            "retrieve(%r…) → dense=%d bm25=%d fused=%d final=%d",
            query[:50], len(dense), len(bm25), len(fused), len(result),
        )
        return result

    @staticmethod
    def _filter_by_intent(chunks: list[Chunk], intent: str, top_k: int) -> list[Chunk]:
        """Keep preferred chunks; backfill from others only if needed."""
        if intent == "data":
            preferred_types = {"csv", "excel"}
        else:  # "policy"
            preferred_types = {"text", "pdf"}

        preferred = [c for c in chunks if c.metadata.document_type in preferred_types]
        others = [c for c in chunks if c.metadata.document_type not in preferred_types]

        if len(preferred) >= top_k:
            return preferred
        return preferred + others[: top_k - len(preferred)]

    # ── Dense search ──────────────────────────────────────────────────────────

    def _dense_search(
        self, query: str, col: Collection, top_k: int
    ) -> list[tuple[Chunk, float]]:
        """Embed query and run ANN search in Chroma."""
        count = col.count()
        if count == 0:
            return []

        query_vec: list[float] = self._encoder.encode(
            [query], normalize_embeddings=True
        )[0].tolist()

        try:
            results = col.query(
                query_embeddings=[query_vec],
                n_results=min(top_k, count),
                include=["documents", "metadatas", "distances"],
            )
        except Exception as exc:
            logger.error("Chroma query error: %s", exc)
            return []

        ids = results["ids"][0]
        documents = results["documents"][0]   # type: ignore[index]
        metadatas = results["metadatas"][0]   # type: ignore[index]
        distances = results["distances"][0]   # type: ignore[index]

        output: list[tuple[Chunk, float]] = []
        for chunk_id, doc, meta, dist in zip(ids, documents, metadatas, distances):
            try:
                chunk = Chunk.from_chroma_record(
                    chunk_id=chunk_id,
                    document=doc,
                    chroma_meta=meta,
                )
                # Chroma cosine distance ∈ [0, 2]; convert to similarity ∈ [-1, 1]
                output.append((chunk, 1.0 - dist))
            except Exception as exc:
                logger.warning("Skipping malformed Chroma record %s: %s", chunk_id, exc)

        return output

    # ── BM25 search ───────────────────────────────────────────────────────────

    def _bm25_search(self, query: str, top_k: int) -> list[tuple[Chunk, float]]:
        """Score indexed chunks with BM25Okapi and return top-k."""
        if self._bm25 is None or not self._bm25_chunks:
            return []

        scores: np.ndarray = self._bm25.get_scores(query.lower().split())
        top_idx = np.argsort(scores)[::-1][:top_k]

        return [
            (self._bm25_chunks[i], float(scores[i]))
            for i in top_idx
            if scores[i] > 0.0
        ]

    # ── Reranking ─────────────────────────────────────────────────────────────

    def _rerank(self, query: str, chunks: list[Chunk], top_k: int) -> list[Chunk]:
        """Cross-encoder rerank with flashrank; falls back to RRF order on error."""
        if not chunks:
            return []
        if len(chunks) == 1:
            return chunks

        if self._ranker is None:
            from flashrank import Ranker
            logger.info("Loading reranker: %s", settings.reranker_model)
            self._ranker = Ranker(model_name=settings.reranker_model)

        from flashrank import RerankRequest
        passages = [{"id": i, "text": c.content[:2048]} for i, c in enumerate(chunks)]
        try:
            response = self._ranker.rerank(RerankRequest(query=query, passages=passages))
            return [chunks[item["id"]] for item in response[:top_k]]
        except Exception as exc:
            logger.warning("Rerank failed (%s) — using RRF order", exc)
            return chunks[:top_k]

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _get_or_create_collection(self, name: str) -> Collection:
        return self._chroma.get_or_create_collection(
            name=name,
            metadata={"hnsw:space": "cosine"},
        )

    def _resolve_collection(self, name: str | None) -> Collection:
        if name is None or name == self.collection_name:
            return self._collection
        return self._get_or_create_collection(name)

    def _collect_columns(self, chunks: list[Chunk]) -> None:
        """Extract column names from CSV/Excel chunks and rebuild the regex."""
        for c in chunks:
            if c.metadata.document_type in ("csv", "excel") and c.metadata.column_names:
                self._indexed_columns.update(c.metadata.column_names)
        self._rebuild_column_pattern()

    def _rebuild_column_pattern(self) -> None:
        """Build a case-insensitive regex from all indexed column names."""
        if not self._indexed_columns:
            self._data_columns_re = None
            return
        escaped = sorted(re.escape(col) for col in self._indexed_columns)
        self._data_columns_re = re.compile(
            r"(?i)\b(" + "|".join(escaped) + r")\b"
        )
        logger.debug("Column pattern rebuilt with %d terms", len(self._indexed_columns))

    def _rebuild_bm25(self) -> None:
        self._bm25 = BM25Okapi([c.bm25_tokens() for c in self._bm25_chunks])
        logger.debug("BM25 rebuilt with %d documents", len(self._bm25_chunks))

    def _warm_bm25(self, col: Collection) -> None:
        """Read all documents from Chroma and rebuild the in-memory BM25 index."""
        if col.count() == 0:
            logger.warning("Chroma collection %r is empty", col.name)
            return

        try:
            result = col.get(include=["documents", "metadatas"])
        except Exception as exc:
            logger.error("Failed to read Chroma for BM25 warm: %s", exc)
            return

        ids = result["ids"]
        documents = result["documents"] or []
        metadatas = result["metadatas"] or []

        chunks: list[Chunk] = []
        for chunk_id, doc, meta in zip(ids, documents, metadatas):
            try:
                chunks.append(
                    Chunk.from_chroma_record(
                        chunk_id=chunk_id, document=doc, chroma_meta=meta
                    )
                )
            except Exception as exc:
                logger.warning("Skipping malformed record %s during warm: %s", chunk_id, exc)

        self._bm25_chunks = chunks
        if chunks:
            self._rebuild_bm25()
            self._collect_columns(chunks)
            logger.info("BM25 warmed with %d chunks", len(chunks))


# ── Reciprocal Rank Fusion ─────────────────────────────────────────────────────

def _reciprocal_rank_fusion(
    ranked_lists: list[list[tuple[Chunk, float]]],
    top_k: int,
    k: int = 60,
) -> list[Chunk]:
    """Merge ranked result lists via Reciprocal Rank Fusion (Cormack et al. 2009).

    Each list contributes ``1 / (k + rank + 1)`` per chunk; scores are summed
    across lists.  Ties are broken by chunk_id for determinism.

    Args:
        ranked_lists: (Chunk, score) pairs, each sorted best-first.
        top_k:        Maximum chunks to return.
        k:            Smoothing constant (default 60).
    """
    rrf: dict[str, float] = {}
    index: dict[str, Chunk] = {}

    for ranked in ranked_lists:
        for rank, (chunk, _) in enumerate(ranked):
            cid = chunk.chunk_id
            rrf[cid] = rrf.get(cid, 0.0) + 1.0 / (k + rank + 1)
            index[cid] = chunk

    ordered = sorted(rrf, key=lambda cid: rrf[cid], reverse=True)
    return [index[cid] for cid in ordered[:top_k]]
