# eval_retrieval.py
#
# Retrieval-only evaluation — no LLM calls, completely free.
#
# Indexes the FULL T2-RAGBench FinQA test context pool (380 unique contexts,
# ~2,500 chunks) into an ephemeral HybridRetriever, then runs 200 queries
# (first 200 questions from the test split) against that full pool.
#
# Metrics reported (n=200 queries, 380-doc corpus):
#   Recall@1, @3, @5 — did the gold context appear in top-k?
#   MRR@3           — 1/rank if gold in top-3, else 0
#   nDCG@5          — discounted gain for single-relevant-doc case
#
# "Gold context" = the chunk whose source_path stem matches the query's
# context_id field from the benchmark.
#
# Usage:
#   uv run python eval_retrieval.py

from __future__ import annotations

import logging
import math
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

logging.basicConfig(level=logging.WARNING, format="[%(levelname)s] %(message)s",
                    stream=sys.stdout)
for lib in ("chromadb", "sentence_transformers", "httpx", "httpcore",
            "huggingface_hub", "aether", "datasets", "filelock", "fsspec"):
    logging.getLogger(lib).setLevel(logging.ERROR)

CONTEXT_DIR = Path("data/finqa_contexts")
TOP_K       = 5    # retrieve top-5 for all cutoffs
N_QUERIES   = 200

# ── Step 1: stream full test split, build unique context pool ─────────────────

print("=" * 68)
print("FinQA Retrieval Eval — 380-doc pool, 200 queries")
print("=" * 68)

print("\n[1/5] Streaming T2-RAGBench FinQA test split …")

from datasets import load_dataset

ds = load_dataset("G4KMU/t2-ragbench", "FinQA", split="test", streaming=True)

contexts: dict[str, str] = {}   # context_id → text
questions: list[dict]   = []    # ordered list of all questions

for row in ds:
    cid = row["context_id"]
    if cid not in contexts:
        contexts[cid] = row.get("context") or ""
    questions.append({
        "id":         row["id"],
        "context_id": cid,
        "question":   row["question"],
        "gold":       str(row.get("program_answer", "")),
    })

print(f"  {len(questions)} questions → {len(contexts)} unique contexts")
print(f"  First {N_QUERIES} questions use {len({q['context_id'] for q in questions[:N_QUERIES]})} distinct contexts")

# ── Step 2: write each unique context to a txt file ───────────────────────────

print(f"\n[2/5] Writing {len(contexts)} context files to {CONTEXT_DIR}/ …")
CONTEXT_DIR.mkdir(parents=True, exist_ok=True)

for ctx_id, text in contexts.items():
    (CONTEXT_DIR / f"{ctx_id}.txt").write_text(text, encoding="utf-8")

print(f"  Done.")

# ── Step 3: index all contexts into ephemeral retriever ───────────────────────

print(f"\n[3/5] Indexing {len(contexts)} contexts (ephemeral store) …")

from aether.ingestion.loader import DocumentLoader
from aether.rag.retriever import HybridRetriever

loader    = DocumentLoader()
retriever = HybridRetriever(ephemeral=True)

t_idx_start = time.time()
all_chunks = []
for path in sorted(CONTEXT_DIR.glob("*.txt")):
    try:
        chunks = loader.load(path)
        all_chunks.extend(chunks)
    except Exception as exc:
        print(f"  WARN: failed to load {path.name}: {exc}")

retriever.index(all_chunks)
t_idx = time.time() - t_idx_start

print(f"  {len(all_chunks)} chunks indexed in {t_idx:.1f}s")

# ── Step 4: run 200 queries ───────────────────────────────────────────────────

print(f"\n[4/5] Running {N_QUERIES} queries against {len(all_chunks)}-chunk pool …")

query_set = questions[:N_QUERIES]

# Metrics accumulators
recall_at = defaultdict(int)  # {1: count, 3: count, 5: count}
mrr_sum   = 0.0
ndcg_sum  = 0.0

# Per-query detail for inspection
details: list[dict] = []

t_q_start = time.time()
for i, q in enumerate(query_set, start=1):
    gold_ctx_id = q["context_id"]
    try:
        chunks = retriever.retrieve(q["question"], top_k=TOP_K)
    except Exception as exc:
        print(f"  WARN [{i:03d}]: retrieve failed: {exc}")
        chunks = []

    # Find rank of gold context (1-indexed; None if not found)
    gold_rank = None
    for rank_0, chunk in enumerate(chunks):
        stem = Path(chunk.metadata.source_path).stem
        if stem == gold_ctx_id:
            gold_rank = rank_0 + 1
            break

    hit1 = 1 if gold_rank == 1 else 0
    hit3 = 1 if (gold_rank is not None and gold_rank <= 3) else 0
    hit5 = 1 if (gold_rank is not None and gold_rank <= 5) else 0
    rr3  = (1.0 / gold_rank) if (gold_rank is not None and gold_rank <= 3) else 0.0
    # nDCG@5: for single relevant doc, IDCG@5 = 1/log2(2) = 1.0
    ndcg5 = (1.0 / math.log2(gold_rank + 1)) if (gold_rank is not None and gold_rank <= 5) else 0.0

    recall_at[1] += hit1
    recall_at[3] += hit3
    recall_at[5] += hit5
    mrr_sum      += rr3
    ndcg_sum     += ndcg5

    details.append({
        "i":          i,
        "id":         q["id"],
        "context_id": gold_ctx_id,
        "gold_rank":  gold_rank,
        "hit@1": hit1, "hit@3": hit3, "hit@5": hit5,
        "rr3":   rr3,  "ndcg5": round(ndcg5, 4),
        "retrieved_ids": [Path(c.metadata.source_path).stem for c in chunks],
    })

    if i % 20 == 0:
        print(f"  {i:03d}/{N_QUERIES}  running R@5={recall_at[5]/i:.3f}")

t_q = time.time() - t_q_start

# ── Step 5: report ────────────────────────────────────────────────────────────

N = N_QUERIES
print(f"\n[5/5] Results (n={N}, {len(all_chunks)}-chunk pool, {len(contexts)}-doc pool):")
print()
print("=" * 68)
print("RETRIEVAL METRICS")
print("=" * 68)
print(f"  Recall@1   : {recall_at[1]/N:.4f}  ({recall_at[1]}/{N})")
print(f"  Recall@3   : {recall_at[3]/N:.4f}  ({recall_at[3]}/{N})")
print(f"  Recall@5   : {recall_at[5]/N:.4f}  ({recall_at[5]}/{N})")
print(f"  MRR@3      : {mrr_sum/N:.4f}")
print(f"  nDCG@5     : {ndcg_sum/N:.4f}")
print()
print(f"  Query time : {t_q:.1f}s total, {t_q/N*1000:.0f}ms/query")
print(f"  Index time : {t_idx:.1f}s for {len(all_chunks)} chunks")
print()

# Misses: gold not in top-5
misses = [d for d in details if d["gold_rank"] is None]
print(f"  Gold not in top-5: {len(misses)}/{N}")
if misses[:5]:
    print("  Sample misses:")
    for m in misses[:5]:
        print(f"    [{m['i']:03d}] {m['id']}  ctx={m['context_id']}")
        print(f"         retrieved: {m['retrieved_ids']}")

# Rank distribution
rank_dist = defaultdict(int)
for d in details:
    rank_dist[d["gold_rank"] or "miss"] += 1
print()
print("  Rank distribution:")
for r in [1, 2, 3, 4, 5, "miss"]:
    print(f"    rank={r}: {rank_dist[r]}")

# Save full detail
import json
out = Path(__file__).parent / "results" / "eval_retrieval_200.json"
out.write_text(json.dumps(details, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"\n  Full detail → {out}")
