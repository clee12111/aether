# scratch/test_retriever_isolation.py
# Proves reset_index() gives per-case corpus isolation.
# Run BEFORE the full suite to confirm no leaks.
import sys, pathlib, logging
logging.basicConfig(level=logging.WARNING)
logging.getLogger("aether.rag").setLevel(logging.INFO)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from aether.runtime import AetherRuntime
from aether.ingestion.loader import DocumentLoader

DEMO = pathlib.Path(__file__).parents[1] / "data/demo"
FILE_A = str(DEMO / "fund_capital_accounts.csv")
FILE_B = str(DEMO / "multi_quarter_transactions.csv")

loader = DocumentLoader()

# eval_mode=True: EphemeralClient — starts fresh, no ./chroma_db pollution
rt = AetherRuntime(eval_mode=True)

def source_names(chunks):
    return [c.metadata.source_path.replace("\\", "/").split("/")[-1] for c in chunks]

print(f"\n[INIT] Chroma collection size (ephemeral, should be 0): "
      f"{rt.retriever._collection.count()}")

# ── Round 1: index case A, retrieve, then reset ───────────────────────────────
print("\n=== Round 1: Case A (fund_capital_accounts.csv) ===")
chunks_a = loader.load(FILE_A)
rt.retriever.index(chunks_a)
print(f"  After index A: BM25={len(rt.retriever._bm25_chunks)}, "
      f"Chroma={rt.retriever._collection.count()}")

results_a = rt.retriever.retrieve("ownership percentage exceeding 20 partner")
names_a = source_names(results_a)
print(f"  retrieve('ownership percentage...') -> {names_a}")
case_a_clean = all("fund_capital_accounts" in n for n in names_a)
print(f"  Only case A sources? {case_a_clean}")

# Reset between cases
rt.retriever.reset_index()
print(f"  After reset_index(): BM25={len(rt.retriever._bm25_chunks)}, "
      f"Chroma={rt.retriever._collection.count()} (must be 0)")

# ── Round 2: index case B only, retrieve ─────────────────────────────────────
print("\n=== Round 2: Case B only (multi_quarter_transactions.csv) ===")
chunks_b = loader.load(FILE_B)
rt.retriever.index(chunks_b)
print(f"  After index B: BM25={len(rt.retriever._bm25_chunks)}, "
      f"Chroma={rt.retriever._collection.count()}")

results_b = rt.retriever.retrieve("duplicate transactions fees suspicious")
names_b = source_names(results_b)
print(f"  retrieve('duplicate transactions fees...') -> {names_b}")

case_a_leaked = any("fund_capital_accounts" in n for n in names_b)
orphan_leaked = any(
    "fund_capital_accounts" not in n and "multi_quarter_transactions" not in n
    for n in names_b
)
print(f"  Case A chunks in case B retrieval : {case_a_leaked}")
print(f"  Orphan chunks in case B retrieval : {orphan_leaked}")

# ── Verdict ───────────────────────────────────────────────────────────────────
print("\n=== VERDICT ===")
passed = case_a_clean and not case_a_leaked and not orphan_leaked
if passed:
    print("  ISOLATION CONFIRMED — reset_index() provides clean per-case corpus")
    print("  Safe to run the full eval suite.")
else:
    if not case_a_clean:
        print("  FAIL: Case A retrieval returned wrong-corpus chunks")
    if case_a_leaked:
        print("  FAIL: Case A docs leaked into Case B retrieval")
    if orphan_leaked:
        print("  FAIL: Orphan docs from previous sessions leaked into Case B retrieval")
    sys.exit(1)
