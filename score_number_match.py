# score_number_match.py
#
# Number-Match scorer for T2-RAGBench / FinQA convention.
# Re-scores the 15 already-produced answers in eval_t2ragbench_15.json
# without re-running the agent.
#
# Normalization rules:
#   1. Extract all candidate numeric values from the model answer string.
#   2. For yes/no answers map to 1/0.
#   3. Direct comparison: relative tolerance 1 % (plus tiny absolute floor).
#   4. /100 normalization: ONLY when abs(gold) <= 1.0 AND abs(candidate) > 1.5
#      — this prevents gold=1.1197 (record 05) from accidentally matching
#        111.97/100=1.1197 via format-mismatch normalization.
#
# Usage:
#   uv run python score_number_match.py

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

# ── Number-Match core ──────────────────────────────────────────────────────────

_SCALE = {
    "million": 1e6, "millions": 1e6, "m": 1e6,
    "billion": 1e9, "billions": 1e9, "b": 1e9,
    "thousand": 1e3, "thousands": 1e3, "k": 1e3,
}
_NUM_RE = re.compile(
    r"""
    (?<![a-zA-Z\d])         # not preceded by word/digit
    ([+-]?)                  # optional sign
    ([\d,]+(?:\.\d+)?)       # integer-or-decimal with optional commas
    \s*
    (%|[a-zA-Z]+)?           # optional suffix (%, million, K, …)
    (?![a-zA-Z\d])           # not followed by word/digit
    """,
    re.VERBOSE,
)

_REL_TOL  = 0.01   # 1 % relative tolerance
_ABS_TOL  = 1e-6   # absolute floor for near-zero golds


def _rel_close(a: float, b: float) -> bool:
    """True if a and b agree within _REL_TOL (relative) or _ABS_TOL."""
    if b == 0:
        return abs(a) < _ABS_TOL
    return abs(a - b) / abs(b) < _REL_TOL


def _extract_candidates(text: str) -> list[float]:
    """
    Return every parseable number from the model answer string.
    Scale suffixes (%, million, K …) are stripped — we return raw floats.
    """
    candidates: list[float] = []
    for m in _NUM_RE.finditer(text):
        sign_str, num_str, suffix = m.group(1), m.group(2), m.group(3)
        try:
            val = float(num_str.replace(",", ""))
        except ValueError:
            continue
        if sign_str == "-":
            val = -val
        # apply explicit scale words (suffix already stripped of %)
        if suffix:
            scale = _SCALE.get(suffix.lower())
            if scale:
                val *= scale
            # % suffix: DON'T divide here — we handle normalisation below
        candidates.append(val)
    return candidates


def _check_boolean(text: str, gold: float) -> bool | None:
    """
    If gold is 0 or 1 and the answer contains a clear yes/no, return match.
    Returns None if no clear boolean signal found.
    """
    low = text.lower()
    has_yes = bool(re.search(r"\byes\b", low))
    has_no  = bool(re.search(r"\bno\b",  low))
    if has_yes and not has_no:
        return _rel_close(1.0, gold)
    if has_no and not has_yes:
        return _rel_close(0.0, gold)
    return None


def number_match(model_answer: str, gold_str: str) -> tuple[bool, str]:
    """
    Return (match: bool, reason: str).

    Normalization applied:
    - yes/no → 1/0  for boolean golds
    - candidate/100  ONLY when abs(gold) <= 1.0 AND abs(candidate) > 1.5
      (catches "14.46%" vs gold=0.1446; blocks "111.97" vs gold=1.1197)
    """
    # Parse gold
    try:
        gold = float(gold_str)
    except ValueError:
        return False, f"unparseable gold: {gold_str!r}"

    # ── Boolean path ──────────────────────────────────────────────────────────
    if gold in (0.0, 1.0):
        bool_result = _check_boolean(model_answer, gold)
        if bool_result is not None:
            if bool_result:
                return True, f"boolean match (gold={gold:.0f})"
            else:
                return False, f"boolean mismatch (gold={gold:.0f})"

    # ── Numeric path ──────────────────────────────────────────────────────────
    candidates = _extract_candidates(model_answer)
    if not candidates:
        return False, "no numeric candidates extracted"

    reasons: list[str] = []
    for c in candidates:
        # 1. Direct comparison
        if _rel_close(c, gold):
            return True, f"direct match: candidate={c} ≈ gold={gold}"

        # 2. /100 normalization — ONLY for ratio-range golds
        #    Condition: abs(gold) <= 1.0 ensures gold is genuinely a decimal ratio.
        #    Condition: abs(c) > 1.5 ensures candidate is meaningfully "larger".
        if abs(gold) <= 1.0 and abs(c) > 1.5:
            c_norm = c / 100.0
            if _rel_close(c_norm, gold):
                return True, f"/100 match: candidate={c}, /100={c_norm:.6g} ≈ gold={gold}"

        reasons.append(f"{c}")

    return False, f"no match (tried: {', '.join(reasons[:6])}; gold={gold})"


# ── Re-score 15 saved results ──────────────────────────────────────────────────

RESULTS_FILE = Path("eval_t2ragbench_15.json")

records: list[dict] = json.loads(RESULTS_FILE.read_text(encoding="utf-8"))

KNOWN_GENUINE_ERRORS = {
    5: "wrong formula (111.97 dollar-diff ≠ 1.1197 return-diff)",
    6: "benchmark artifact (8.61% arithmetic ≠ 6.76% gold)",
    10: "wrong row (-530 ≠ -35)",
}

print("=" * 72)
print("Number-Match Re-Score  —  15 T²-RAGBench FinQA records")
print("=" * 72)
print(f"{'#':>2}  {'ID':<20} {'GOLD':<22} {'MATCH':<6}  REASON")
print("-" * 72)

matches = 0
for r in records:
    i          = r["i"]
    rec_id     = r["id"]
    gold_str   = r["gold"]
    model_ans  = r["model_answer"]

    matched, reason = number_match(model_ans, gold_str)
    if matched:
        matches += 1

    mark     = "✓" if matched else "✗"
    genuine  = "  ← GENUINE ERROR" if i in KNOWN_GENUINE_ERRORS else ""
    print(f"{i:>2}  {rec_id:<20} {gold_str:<22} {mark:<6}  {reason}{genuine}")

print("-" * 72)
print(f"\nCORRECTED SCORE:  {matches} / {len(records)}")

# Verify the 3 genuine errors
print()
print("Genuine-error audit:")
for i, desc in KNOWN_GENUINE_ERRORS.items():
    r = next(x for x in records if x["i"] == i)
    matched, reason = number_match(r["model_answer"], r["gold"])
    status = "STILL WRONG ✓" if not matched else "OOPS — scorer too loose ✗"
    print(f"  Record {i:02d} ({r['id']}): {status}")
    print(f"    gold={r['gold']}  |  {desc}")
    print(f"    scorer says: {reason}")
