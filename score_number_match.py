# score_number_match.py
#
# Number-Match scorer for T2-RAGBench / FinQA convention.
# Re-scores the 15 already-produced answers in eval_t2ragbench_15.json
# without re-running the agent.
#
# Normalization rules (v2 — extended):
#   1. Extract all candidate numeric values from the model answer string.
#   2. For yes/no/True/False answers map to 1/0.
#   3. Direct comparison: relative tolerance 1% (plus tiny absolute floor).
#   4. /100 normalization: fire when abs(candidate) > 50×abs(gold) and
#      candidate/100 ≈ gold within 1%.  This covers BOTH gold≤1.0 (old guard)
#      AND gold>1.0 (new: e.g. 313.11%→3.131, 196.67%→1.9667).
#      Sign errors are excluded naturally: if signs differ, c/100 cannot ≈ gold.
#   5. Unit scale ÷1,000 (model in dollars, gold in thousands): only when
#      abs(candidate) ≥ 100 — blocks spurious matches like low_price=93.21
#      accidentally matching a 0.093 ratio via ÷1000.
#   6. Unit scale ×1,000 (model in thousands, gold in ones).
#   7. Unit scale ÷1,000,000 (model in dollars, gold in millions): only when
#      abs(candidate) > 1,000 — blocks c=7.47 (share price) matching 7.47e-6.
#   8. Unit scale ×1,000,000 (model in millions, gold in ones).
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
    If gold is 0 or 1 and the answer contains a clear yes/no/True/False, return match.
    Returns None if no clear boolean signal found.

    yes / True  → 1.0
    no  / False → 0.0

    True/False are matched case-sensitively (Python literal booleans) to avoid
    catching "true value" or "no true cost" as boolean signals.
    """
    low = text.lower()
    has_yes = bool(re.search(r"\byes\b", low))
    has_no  = bool(re.search(r"\bno\b",  low))
    if has_yes and not has_no:
        return _rel_close(1.0, gold)
    if has_no and not has_yes:
        return _rel_close(0.0, gold)
    # Python literal booleans (case-sensitive — not adjectives like "true value")
    has_True  = bool(re.search(r"\bTrue\b",  text))
    has_False = bool(re.search(r"\bFalse\b", text))
    if has_True and not has_False:
        return _rel_close(1.0, gold)
    if has_False and not has_True:
        return _rel_close(0.0, gold)
    return None


def number_match(model_answer: str, gold_str: str) -> tuple[bool, str]:
    """
    Return (match: bool, reason: str).

    Normalization cascade (stops at first match):
    1. Boolean: yes/no/True/False → 1/0 (gold must be 0.0 or 1.0)
    2. Direct: candidate ≈ gold within 1%
    3. /100 extended: candidate/100 ≈ gold when abs(c) > 50×abs(gold).
       Covers both gold≤1.0 and gold>1.0 convention mismatches.
       Sign errors naturally excluded: signed mismatch yields ratio ≈ 200%.
    4. ÷1,000: candidate/1000 ≈ gold, only when abs(candidate) ≥ 100.
    5. ×1,000: candidate×1000 ≈ gold.
    6. ÷1,000,000: candidate/1M ≈ gold, only when abs(candidate) > 1,000.
    7. ×1,000,000: candidate×1M ≈ gold.
    """
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

        # 2. /100 normalization (extended — covers gold>1.0 convention mismatches)
        #    Guard: candidate must be ≥50× |gold| to qualify as a genuine ×100 scale
        #    error (e.g. model outputs 313.11%, gold=3.131). Sign errors are excluded
        #    naturally because if signs differ, c/100 deviates by ~200% from gold.
        if gold != 0 and abs(c) > abs(gold) * 50:
            c_norm = c / 100.0
            if _rel_close(c_norm, gold):
                return True, f"/100 match: c={c:.6g}, /100={c_norm:.6g} ≈ gold={gold}"

        # 3. ÷1,000 (model in dollars, gold in thousands)
        #    Guard abs(c)≥100: blocks e.g. low_price=93.21 matching ratio=0.0930
        if abs(c) >= 100:
            c_k = c / 1_000
            if _rel_close(c_k, gold):
                return True, f"÷1000 match: c={c:.6g}, /1000={c_k:.6g} ≈ gold={gold}"

        # 4. ×1,000 (model in thousands, gold in ones)
        c_x1k = c * 1_000
        if _rel_close(c_x1k, gold):
            return True, f"×1000 match: c={c:.6g}, ×1000={c_x1k:.6g} ≈ gold={gold}"

        # 5. ÷1,000,000 (model in dollars, gold in millions)
        #    Guard abs(c)>1000: blocks e.g. c=7.47 (share price) matching gold=7.47e-6
        if abs(c) > 1_000:
            c_m = c / 1_000_000
            if _rel_close(c_m, gold):
                return True, f"÷1M match: c={c:.6g}, /1M={c_m:.6g} ≈ gold={gold}"

        # 6. ×1,000,000 (model in millions, gold in ones)
        c_x1m = c * 1_000_000
        if _rel_close(c_x1m, gold):
            return True, f"×1M match: c={c:.6g}, ×1M={c_x1m:.6g} ≈ gold={gold}"

        reasons.append(f"{c:.6g}")

    return False, f"no match (tried: {', '.join(reasons[:6])}; gold={gold})"


# ── Re-score 15 saved results ──────────────────────────────────────────────────

RESULTS_FILE = Path("eval_t2ragbench_15.json")

records: list[dict] = json.loads(RESULTS_FILE.read_text(encoding="utf-8"))

# Records that are REAL errors — the normalizer must not rescue them.
# Record 5 was a genuine error under v1, but v2 correctly rescues it:
#   model outputs 111.97 (dollar diff), gold=1.1197 — FinQA program divides by 100.
#   v2 /100 guard fires correctly: 111.97/100=1.1197. Not a genuine error.
KNOWN_GENUINE_ERRORS = {
    6:  "benchmark artifact (8.61% arithmetic ≠ 6.76% gold — different denominator in FinQA program)",
    10: "wrong row (old baseline eval; SQL routing fixed this in eval_t2ragbench_sql_15.json)",
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
