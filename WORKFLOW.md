# WORKFLOW.md — Dual-Claude Operating Rules

Human (operator) + Claude.ai (advisor) + Claude Code (engineer). Project-agnostic.
Canonical copy lives user-level at `~/.claude/WORKFLOW.md`; the optional per-project bar lives
in each repo's `FRONTIER.md`.

## The living docs — what a cold chat reads, and keeps updated

A new advisor or engineer chat must resume from *files*, never from a previous chat's memory. The
document set is the project's memory; keeping it current is part of the work, not an afterthought. A
fresh chat reads these in order and can pick up cold:

1. **CLAUDE.md / PROJECT.md** — stable foundations: what the project is, the hard rules/constraints,
   the foundational decisions that rarely change. Loaded first, every session. Points to DECISION.md
   for the live state.
2. **DECISION.md** — the running ledger. *Every* non-trivial decision or result, dated,
   newest-at-top, in the form **decision / why / precludes**. Supersede entries (strike-through +
   note), never delete — the trail is the value, and it doubles as the raw material for any writeup
   or "what I built / what I cut / what's next" summary.
3. **WORKFLOW.md** — this file: the agnostic operating rules. Does not change per project.
4. **FRONTIER.md** — the optional per-project bar (see below). Present only for consequence-dense work.

**The ledger rule (self-enforcing, not memory-dependent):** the advisor appends to DECISION.md *at
the moment* of each non-trivial decision or result — not reconstructed later. A decision that isn't
in the ledger didn't happen. If both advisor and engineer maintain it, the engineer's live entries
(closest to ground truth) are canonical; reconcile at the next commit. This rule is what lets any
chat be dropped and resumed — so it belongs here, in the agnostic file, not in a per-project doc.

Lean variant: small or short projects fold CLAUDE.md into PROJECT.md and skip FRONTIER.md — but
DECISION.md stays. The ledger is the one non-negotiable.

## The irreducible core — never skipped, even rushing

Failure 3 (median passed off as frontier) hits hardest under time pressure. These cost seconds,
they caught the real failures, and the `frontier-audit` skill auto-fires them on any sign-off:

1. **Approach-landscape.** "What are the *families* of approaches to this, and which is frontier?
   What did we not consider?" Non-numeric frontier (e.g. enumeration vs. property + fuzzing) is
   invisible to a SOTA-*number* check — this is the step that catches it.
2. **Frontier-review, not approval.** Ask "what would a top-1% practitioner say is *wrong* with
   this?" — never "is this good?" (Judgment regresses to median when asked the soft question.)
3. **Median-fallback confession.** The engineer lists every place it built median where more was
   warranted, with the reason. "Done" that hasn't confessed its fallbacks is not done.

## Triggering — bake the skill call into the prompt (don't trust the auto-trigger)

The skill's description-based auto-trigger is model judgment, and tested, **it silently skipped.**
The reliable trigger is the prompt itself, because the advisor writes every engineer prompt:

- **Every non-trivial build prompt ENDS with the call explicitly:** *"When done, run `frontier-audit`
  and report its verdict before declaring complete."*
- **Consequence-dense / shipping work OPENS with:** *"Run `frontier-bar` first (separate context);
  build against FRONTIER.md."*

This is **authoring-deterministic** (the advisor always writes it in — no judgment, no memory gap)
but not **execution-deterministic** (the engineer must still run it). So keep two backstops, in
order of reliability: a **commit-gate hook** (fires on the event, catches the execution-drop case)
and the human's **frontier-review question** (depends on nothing — the true floor). Primary =
prompt; backstop = hook; floor = the human. Don't rely on the auto-trigger as anything but a bonus.

## The core asymmetry

Verification is the bottleneck, not generation. Generators are cheap; output that can't be
verified above the median is the failure. The advisor has context but not the repo; the engineer
has the repo but not the framing; the human alone briefly has both, when relaying. The workflow's
job is to **make the frontier gap visible and forceable** — not to pretend anyone in the loop
reliably knows the frontier. Nobody does.

## The honest ceiling — state it on every axis

Live research + an independent bar-setter raise the *floor* to **best-published practice**. They
do NOT reach true/proprietary frontier: both Claudes share the same training + web-index bound, so
separating proposer from bar-setter kills the *self-serving* bar, not the *knowledge* bar. Every
axis's bar is therefore "best published; true frontier unknown." On unpublished-edge domains (HFT
execution) say so plainly: `bar confidence: proprietary-unknown`. A measured pass against the
published bar must never masquerade as the real frontier.

## The three roles

- **Human** — owns the **consequence map** (what matters, in real units, and why — irreducibly
  yours) and **forces disclosure** via the disqualifying questions. Final authority. Does NOT need
  to know the frontier; needs to make the system *show* it. (Your skeptical question is what caught
  the last failure — that is the strength, not a gap.)
- **Advisor (Claude.ai)** — interprets, frames, makes architectural calls, writes prompts FOR the
  engineer; does not write code. Context but not the repo. Strong judgment, weak ground truth →
  risk of stale assumptions.
- **Engineer (Claude Code)** — reads/writes/runs the real repo. Ground truth, weak framing → risk
  of solving an imagined system when context-starved.

Neither Claude has the full picture alone. The human bridges.

## The three failure modes (all observed)

1. **Engineer context starvation** — circles 3+ times, each fix locally sensible, solves an
   imagined system. Cause: the prompt lacked the WHY. Counter: every non-trivial prompt carries the
   constraint/finding inline, by name.
2. **Advisor stale assumptions** — confident spec, wrong in three places. Cause: wrote from docs,
   not recon. Counter: a recon round before any invasive build — engineer reports what's actually
   there; advisor builds against the report.
3. **Median passed off as frontier** — builds, checks pass, "done" = tutorial-grade, and the human
   can't catch it because the gap sits above their knowledge line. Frontier *vocabulary* present,
   frontier *judgment* absent. NOT a relay failure — the handoff can be flawless and it still
   happens. Counter: the irreducible core above; and for consequence-dense axes, a real bar
   (FRONTIER.md).

## Disqualifying questions — the human's lever

Fire any of these on any sign-off; none require knowing the answer in advance:
- What's the SOTA on this axis (number *or* approach), and who holds it?
- What would a top-1% practitioner find embarrassing here?
- What did you deliberately leave out?
- Where did you fall back to median, and why?
- What's the cheapest experiment that would prove this isn't at the bar?

## The standard cycle (non-trivial changes)

1. Advisor proposes a direction (problem, decision, tradeoffs). 2. Human decides. 3. **Recon**
(mandatory for invasive changes): read-only prompt → engineer reports verbatim → relay. 4. Build
prompt grounded in the recon: lock decisions ("X: Y because Z"), constraints inline, acceptance
checks that test the load-bearing thing, REPORT-BACK required. 5. Engineer executes + reports:
reads first, RUNS the checks (executed ≠ reasoned), quotes changes, flags mismatches, states ABSENT
for missing. 6. Advisor verifies: acceptance landed, silent-failure modes spotted, **frontier-review**
(not "is it good") — and against FRONTIER.md if one exists. 7. Human approves or sends back; fires
the disqualifying questions.

## FRONTIER.md — opt-in heavy machinery (NOT every project)

A full bar is overhead — run it only for **consequence-dense, shipping** axes (where being median
costs real units: money, latency, security). Skip it for learning-scope or low-consequence work;
the irreducible core covers those. (Frontier-ifying a helper script is cosplay too — over-
engineering is its own Failure 3.) Built by the `frontier-bar` skill in a **separate context**
(proposer ≠ bar-setter), via **live research** (not memory), and **re-run at phase transitions**
(the frontier moves; a Phase-0 snapshot goes stale).

FRONTIER.md contains: an **approach landscape** (families of approaches + which is frontier — the
non-numeric axis that catches the fuzzer class of miss); a consequence map (axes by real-units
impact); three concrete tiers per axis (median / industry / frontier); a dated real anchor per
frontier claim (no anchor → `verify`, treat as candidate); `measure:` + cited `reference number:`
on every numeric axis; a divergence log (each fork from median + reason + consequence in the
human's units); and thin/proprietary/stale flags stated plainly.

The bar is executable where it's a number: build the benchmark, store `measure:` next to the cited
`reference number:`. Converts "is this frontier?" (judgment, regresses to median) into "what's the
number vs. reference" (measurement, unfakeable). Reserve prose-judgment only for axes that
genuinely can't be measured.

## Prompt rules (advisor → engineer)

Tight, not dense. WHY inline on every meaningful instruction. Lock real decisions, leave
implementation open. Acceptance checks test the deterministic part. Don't ask the engineer to make
interpretive calls — state the choice or surface it to the human.

## Reporting rules (engineer → advisor)

Quote real code, don't paraphrase. Distinguish executed from reasoned. Flag assumption mismatches
explicitly. State what was removed AND what was kept. Stop and surface if you circle 3+ times. On
"done," run `frontier-audit` and confess median fallbacks before declaring complete.

## Tooling (`~/.claude/skills/`)

- **`frontier-audit`** (lightweight, auto-triggers on sign-off language) — fires the irreducible
  core: approach-landscape, frontier-review question, median-fallback confession, disqualifying
  questions. If a FRONTIER.md exists, also runs its `measure:` commands and audits anchor
  staleness. **Runs the cheap questions even with no FRONTIER.md.** Report-only; ends on a verdict,
  not a bare "done" — and **appends that verdict (named gaps + cheapest next experiment) to
  DECISION.md**, so the ledger rule is enforced by the skill, not just advisor discipline.
- **`frontier-bar`** (heavy, opt-in, manually invoked) — Phase-0 bar-setter, independent of the
  builder. Runs the adversarial approach-landscape + live-research pass; writes the consequence
  map / tiers / `measure:` harness / FRONTIER.md. Never builds the solution. **Appends a bar-set
  entry** (axes tiered, load-bearing anchors, state-vs-bar gap) to DECISION.md.

A skill is only as good as what it enforces: an audit against no bar still fires the cheap core; an
audit against a *thin* bar is cosplay with a passing grade. **The questions are the floor; the bar
is the ceiling.**

---

*Mirrors the project's own thesis (Aegis / Meridian): build the measurement layer first, trust the
numbers it produces, and don't claim more than the delta — and the limits — show.*
