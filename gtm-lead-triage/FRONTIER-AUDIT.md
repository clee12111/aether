## Phase L — Testing + CI/CD: Frontier Audit

### Approach landscape

FRONTIER.md does not contain a Phase L section. The frontier bar was written through
Phase K (Production Observability, K1–K7). Phase L — GitHub Actions CI, end-to-end
FastAPI integration tests, load/concurrency smoke test, coverage floor, and `/metrics`
exposure decision — has no formal spec in FRONTIER.md. This audit assesses Phase L
readiness from first principles against the existing code and test files.

**What exists (current state):**

- `tests/` — 14 test files covering unit and some integration scenarios:
  `test_agency.py`, `test_api_hardening.py`, `test_daily_cap.py`,
  `test_enrichment.py`, `test_extraction.py`, `test_hubspot_crm.py`,
  `test_observability.py`, `test_pdl_provider.py`, `test_pg_store.py`,
  `test_privacy.py`, `test_reliability.py`, `test_security.py`,
  `test_signals.py`, `test_trace_store_parity.py`, `test_waterfall.py`.
- `evals/run_eval.py` — a deterministic CI gate: 5 mock leads, full agent loop,
  asserts tier+route, exits nonzero on any miss. Suitable as a pre-merge gate.
- `gtm_triage/api.py` — FastAPI app with `/health`, `/ready`, `/metrics`,
  `/metrics/outcomes`, `/triage`, `/outcomes/{run_id}`, full middleware stack.
- `gtm_triage/middleware.py` — `_PUBLIC_PATHS` correctly includes `/ready`,
  `/metrics`, and `/metrics/outcomes`. Auth, rate-limit, metrics, request-id
  middleware all present.
- `.github/` — **does not exist** in the project root. There is no CI workflow.
- `pyproject.toml` — **does not exist**. No `setup.cfg`, no `pytest.ini`, no
  `.coveragerc`. No coverage tooling is configured. No test runner config exists.
- No load/concurrency test exists in `tests/` or `scripts/`.

**Phase K observability implementation (relevant to Phase L scope):**
K1–K7 are all implemented. `/ready`, `/metrics`, and `/metrics/outcomes` are in
`_PUBLIC_PATHS`. `test_observability.py` uses `TestClient` with the real lifespan
context (`:memory:` SQLite, mock provider) and covers K1–K7 assertions in code.
These tests are not yet wired into any CI gate.

---

### What's WRONG with current state

**1. No CI workflow — this is the primary gap.**
There is no `.github/workflows/` directory at the project root. Every test that
exists, including the deterministic `evals/run_eval.py` gate and all 14 test files
in `tests/`, runs only when a developer manually runs them. Nothing gates merges to
`main`. Any broken import, middleware regression, or agent-loop change ships
undetected.

**2. No pytest configuration.**
There is no `pyproject.toml`, `pytest.ini`, or `setup.cfg`. Running `pytest` from
the project root has no defined test paths, no ignore patterns (especially `web/`
with its massive `node_modules/` tree), no markers, and no output format. Tests
cannot be reliably reproduced by a CI runner without tribal knowledge of which
directory to `cd` into and which flags to pass. A naive `pytest` from the root
would attempt to collect test files inside `node_modules/`.

**3. No coverage tooling or floor.**
`pytest-cov` or `coverage.py` is not configured anywhere. There is no coverage
floor (e.g., `--cov-fail-under=70`). The project has extensive unit tests but no
mechanism to detect new code paths that aren't tested. Coverage regressions are
invisible.

**4. No load/concurrency smoke test.**
The `tests/` directory has zero concurrency tests. The middleware stack has a
`_TokenBucket` rate limiter (a plain dict, not locked) and the triage endpoint runs
the agent in `asyncio.to_thread`. Under concurrent load, simultaneous refill and
token-deduction operations on `_buckets` could exhibit races on CPython (GIL
provides some protection but dict mutation across threads is not guaranteed safe
when reading and writing happen concurrently without locks). No smoke test validates
that N concurrent `/triage` requests complete without deadlock, 500s, or incorrect
rate-limit behavior.

**5. No full-request-lifecycle end-to-end integration test.**
`test_observability.py` and `test_api_hardening.py` use `TestClient` with the real
app, but they test narrow slices: auth, metrics primitives, outcome API. There is no
test that submits a lead via `POST /triage`, asserts the response shape (`run_id`,
`final_tier`, `final_route` all present), then queries `GET /runs/{run_id}` to
verify the trace was written — i.e., a full round-trip smoke test that would catch
a middleware ordering regression, a lifespan teardown bug, or a trace-store write
failure.

**6. `/metrics` exposure decision is implicit, not documented.**
`/metrics` is public (no auth), returns Prometheus text format, and is confirmed in
`_PUBLIC_PATHS`. FRONTIER.md K3 says "public — same as `/health`" but neither
DECISION.md nor any doc covers what this means for production deployments. In a
real deployment, Prometheus scrape targets are often protected by network policy or
basic auth because the scrape endpoint reveals internal system state (circuit-breaker
positions, daily cap usage, cache hit rates). The current design is exposed-by-default
with no documented security tradeoff.

**7. `evals/run_eval.py` is not wired into the test suite.**
It exits nonzero on failure and is ideal as a CI gate, but it lives outside `tests/`
and is not called by pytest. It must be run separately. In CI, it would need its own
step or be converted to a pytest-compatible test module.

**8. No Python version pin or matrix testing.**
No `pyproject.toml` means no Python version constraint is enforced in the project
metadata. The code uses `from __future__ import annotations` and Python 3.10+ syntax
(`X | Y` type unions in type hints) throughout; running on 3.9 would fail. If CI is
ever misconfigured to use a different Python version, failures would be silent until
an import error surfaces.

---

### Median-fallback confession

A median implementation at this stage would:
- Add a `.github/workflows/ci.yml` that runs `pytest tests/` on push to `main`,
  passes if all tests are green, and calls it done.
- Leave coverage unconfigured ("we'll add it later").
- Skip load testing entirely ("not needed until we have real traffic").
- Copy-paste a generic workflow without wiring in `evals/run_eval.py` as a separate
  gate or adding a `pytest.ini` to prevent `node_modules/` collection.
- Leave `/metrics` public with no documented decision and no note about network-policy
  hardening for multi-tenant production.
- Not write a full round-trip integration test because "the unit tests are sufficient."

This audit is calling out that the above minimal approach misses: (a) a coverage
floor that enforces quality over time, (b) a concurrency smoke test that validates
the thread-safety assumptions in the middleware, (c) a pytest config that makes runs
reproducible without knowing to pass `--ignore=web/`, and (d) a documented decision
on Prometheus endpoint exposure for production.

---

### Verdict

**Phase L is NOT done. The frontier bar is not met.**

The code under test (API, middleware, observability K1–K7, agent loop) is in strong
shape — the 14 test files are substantive, the `TestClient` integration pattern is
correct, `evals/run_eval.py` is a solid deterministic gate. But the CI/CD and
test-infrastructure layer is entirely absent:

| Phase L criterion | Status |
|---|---|
| GitHub Actions CI workflow | NOT STARTED — no `.github/` directory |
| pytest configuration (`pyproject.toml` or `pytest.ini`) | NOT STARTED |
| Coverage floor (`--cov-fail-under`) | NOT STARTED |
| Load/concurrency smoke test | NOT STARTED |
| Full round-trip end-to-end integration test | PARTIAL (narrow slices exist, no round-trip) |
| `/metrics` exposure decision documented | NOT STARTED |
| `evals/run_eval.py` wired into CI | NOT STARTED |

**To reach the frontier bar for Phase L, the following must be built:**

1. `.github/workflows/ci.yml` — runs on push/PR to `main`; steps: checkout,
   install deps (`pip install -e ".[dev]"` or equivalent), `pytest tests/ -x
   --tb=short --cov=gtm_triage --cov-fail-under=70`, then
   `python -m evals.run_eval` as a separate step. Python 3.11 on `ubuntu-latest`.
2. `pyproject.toml` (or `pytest.ini`) — `[tool.pytest.ini_options]` with
   `testpaths = ["tests"]`, `addopts = "--tb=short"`, and
   `norecursedirs = ["web", "node_modules", ".git"]`.
3. Coverage floor at 70% (honest given the mock-heavy test set; raise as coverage
   grows).
4. `tests/test_load.py` — 10 concurrent `POST /triage` requests via
   `concurrent.futures.ThreadPoolExecutor` against the `TestClient`; assert all
   return 200 or 429 (never 500); assert no duplicate `run_id`s in responses.
5. `tests/test_e2e.py` — a full round-trip test: `POST /triage` → assert `run_id`
   in response → `GET /runs/{run_id}` → assert events list is non-empty and
   `final_tier` is one of the valid tiers.
6. DECISION.md update: document that `/metrics` is public by design for single-tenant
   or internal deployments; add a note that for multi-tenant production, add a network
   policy or Nginx basic-auth layer in front of the scrape endpoint.

Current gate: **FAIL** — zero of six Phase L infrastructure criteria are implemented.
