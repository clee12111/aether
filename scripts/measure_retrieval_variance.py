# scripts/measure_retrieval_variance.py

"""Measure retrieval precision@5 variance across multiple runs."""

import json
import math
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aether.ingestion.loader import DocumentLoader
from aether.rag.retriever import HybridRetriever

# ── Paths ────────────────────────────────────────────────────────────────────

CASES_PATH = Path(__file__).resolve().parent.parent / "evals" / "retrieval" / "cases.json"
DEMO_DIR = Path(__file__).resolve().parent.parent / "data" / "demo"
DEMO_FILES = [
    DEMO_DIR / "fund_capital_accounts.csv",
    DEMO_DIR / "fund_agreement.txt",
    DEMO_DIR / "compliance_policy.txt",
]

NUM_RUNS = 5

# ── Category mapping (by 0-based index into cases.json) ─────────────────────
# data (13):      indices 0-8, 10-14  (CSV partner/balance/distribution queries)
# policy (10):    indices 15-24       (fund agreement + compliance policy)
# cross_doc (2):  indices 9, 14       (queries needing both data and policy)
#
# Note: case 9 ("distribution exceeds net income by 5x") is the known failing
# cross-doc case.  Case 14 ("distributions disproportionate to ownership") also
# spans data + policy reasoning.

CATEGORY_MAP: dict[int, str] = {}
for i in range(25):
    if i in (8, 14):
        CATEGORY_MAP[i] = "cross_doc"
    elif 15 <= i <= 24:
        CATEGORY_MAP[i] = "policy"
    else:
        CATEGORY_MAP[i] = "data"

CATEGORY_SIZES = {"data": 13, "policy": 10, "cross_doc": 2}
CATEGORY_ORDER = ["data", "policy", "cross_doc"]


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _stdev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = _mean(values)
    return math.sqrt(sum((v - m) ** 2 for v in values) / (len(values) - 1))


def build_retriever() -> HybridRetriever:
    """Create a fresh retriever and index all demo files."""
    collection = f"variance_eval"
    r = HybridRetriever(collection_name=collection)
    loader = DocumentLoader()
    for f in DEMO_FILES:
        chunks = loader.load(str(f))
        r.index(chunks)
    return r


def evaluate_once(
    retriever: HybridRetriever,
    cases: list[dict],
) -> list[bool]:
    """Run precision@5 on all cases. Returns list of pass/fail booleans."""
    results: list[bool] = []
    for case in cases:
        chunks = retriever.retrieve(case["query"], top_k=5)
        combined = " ".join(" ".join(c.content.split()) for c in chunks)
        passed = case["expected_chunk_contains"] in combined
        results.append(passed)
    return results


def main() -> None:
    cases = json.loads(CASES_PATH.read_text())
    assert len(cases) == 25, f"Expected 25 cases, got {len(cases)}"

    print(f"Loading retriever and indexing {len(DEMO_FILES)} demo files...")
    retriever = build_retriever()
    print("Retriever ready.\n")

    # run_results[run_idx] = list of 25 bools
    run_results: list[list[bool]] = []

    for run_idx in range(NUM_RUNS):
        print(f"  Run {run_idx + 1}/{NUM_RUNS} ...", end=" ", flush=True)
        results = evaluate_once(retriever, cases)
        run_results.append(results)
        passed = sum(results)
        print(f"{passed}/{len(cases)} passed")

    # ── Compute per-run, per-category stats ──────────────────────────────────

    # per_run_cat[run_idx][category] = pass count
    per_run_cat: list[dict[str, int]] = []
    per_run_total: list[int] = []

    for results in run_results:
        cat_counts: dict[str, int] = {c: 0 for c in CATEGORY_ORDER}
        for i, passed in enumerate(results):
            if passed:
                cat_counts[CATEGORY_MAP[i]] += 1
        per_run_cat.append(cat_counts)
        per_run_total.append(sum(results))

    # ── Print table ──────────────────────────────────────────────────────────

    print()
    print(f"Retrieval Variance Report ({NUM_RUNS} runs, {len(cases)} cases)")
    print()
    header = (
        f"{'Run':<10}| {'Data (13)':>10} | {'Policy (10)':>12} "
        f"| {'Cross-doc (2)':>14} | {'Overall (25)':>14}"
    )
    print(header)
    print("-" * len(header))

    for run_idx in range(NUM_RUNS):
        cat = per_run_cat[run_idx]
        total = per_run_total[run_idx]
        pct = total * 100 / len(cases)
        print(
            f"Run {run_idx + 1:<5} "
            f"| {cat['data']:>4}/{CATEGORY_SIZES['data']:<5} "
            f"| {cat['policy']:>5}/{CATEGORY_SIZES['policy']:<6} "
            f"| {cat['cross_doc']:>7}/{CATEGORY_SIZES['cross_doc']:<6} "
            f"| {total:>5}/{len(cases)} ({pct:.0f}%)"
        )

    # ── Summary stats ────────────────────────────────────────────────────────

    totals = [float(t) for t in per_run_total]
    mean_total = _mean(totals)
    std_total = _stdev(totals)
    min_total = int(min(totals))
    max_total = int(max(totals))

    print()
    print("Summary:")
    print(f"  Mean:    {mean_total:.1f}/{len(cases)} ({mean_total * 100 / len(cases):.1f}%)")
    print(f"  Min:     {min_total}/{len(cases)} ({min_total * 100 / len(cases):.1f}%)")
    print(f"  Max:     {max_total}/{len(cases)} ({max_total * 100 / len(cases):.1f}%)")
    print(f"  Stdev:   {std_total:.2f}")

    print()
    print("Per-category means:")
    for cat in CATEGORY_ORDER:
        cat_values = [float(per_run_cat[r][cat]) for r in range(NUM_RUNS)]
        cat_mean = _mean(cat_values)
        cat_std = _stdev(cat_values)
        size = CATEGORY_SIZES[cat]
        label = {"data": "Data queries", "policy": "Policy queries", "cross_doc": "Cross-document"}[cat]
        print(f"  {label + ':':<20} {cat_mean * 100 / size:5.1f}%  (stdev {cat_std * 100 / size:.1f}%)")

    # ── Per-case flip detection ──────────────────────────────────────────────

    print()
    flaky = []
    for i, case in enumerate(cases):
        outcomes = {run_results[r][i] for r in range(NUM_RUNS)}
        if len(outcomes) > 1:
            pass_count = sum(run_results[r][i] for r in range(NUM_RUNS))
            flaky.append((i, case["query"][:60], pass_count))

    if flaky:
        print(f"Flaky cases ({len(flaky)}):")
        for idx, query, pass_count in flaky:
            cat = CATEGORY_MAP[idx]
            print(f"  [{cat}] Case {idx + 1}: {pass_count}/{NUM_RUNS} passed — {query}")
    else:
        print("No flaky cases detected — all cases had identical outcomes across all runs.")


if __name__ == "__main__":
    main()
