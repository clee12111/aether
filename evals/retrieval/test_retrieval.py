# evals/retrieval/test_retrieval.py

"""Retrieval precision@5 eval — checks that the top-5 chunks contain expected content."""

import json
from pathlib import Path

import pytest

from aether.ingestion.loader import DocumentLoader
from aether.rag.retriever import HybridRetriever

CASES_PATH = Path(__file__).parent / "cases.json"
DEMO_DIR = Path(__file__).parents[2] / "data" / "demo"

DEMO_FILES = [
    DEMO_DIR / "fund_capital_accounts.csv",
    DEMO_DIR / "fund_agreement.txt",
    DEMO_DIR / "compliance_policy.txt",
]

_cases = json.loads(CASES_PATH.read_text())


@pytest.fixture(scope="module")
def retriever():
    """Index all demo files once for all retrieval tests."""
    r = HybridRetriever(collection_name="eval_retrieval")
    loader = DocumentLoader()
    for f in DEMO_FILES:
        chunks = loader.load(str(f))
        r.index(chunks)
    return r


@pytest.mark.parametrize(
    "case", _cases, ids=[c["query"][:50] for c in _cases]
)
def test_retrieval(retriever, case):
    results = retriever.retrieve(case["query"], top_k=5)
    combined = " ".join(" ".join(c.content.split()) for c in results)
    assert case["expected_chunk_contains"] in combined, (
        f"Expected '{case['expected_chunk_contains']}' in top-5 chunks for: {case['query']}"
    )


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Print retrieval precision summary after all tests."""
    passed = len(terminalreporter.stats.get("passed", []))
    failed = len(terminalreporter.stats.get("failed", []))
    total = passed + failed
    if total:
        pct = passed * 100 // total
        terminalreporter.write_line(
            f"\nRetrieval precision@5: {passed}/{total} ({pct}%)"
        )
