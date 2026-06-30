"""Live smoke tests against the deployed demo.

Runs against real endpoints — do NOT add to CI without explicit gating.

Usage:
    pytest tests/smoke_live.py -v

    # override targets
    SMOKE_API_URL=https://my-api.onrender.com pytest tests/smoke_live.py -v

Expected pass/fail on the CURRENT deploy:
  test_config_keys       — PASS
  test_ready_all_ok      — PASS  (enrichment probe now does a real PDL call)
  test_triage_datadoghq  — PASS  (tier returned + real PB note created)
  test_ui_loads          — PASS  (Playwright, skipped if playwright not installed)
"""

from __future__ import annotations

import os
import time
import uuid
from urllib.parse import urljoin

import httpx
import pytest

# ── Config ────────────────────────────────────────────────────────────────────

API_URL = os.environ.get("SMOKE_API_URL", "https://aether-1-x999.onrender.com").rstrip("/")
UI_URL  = os.environ.get("SMOKE_UI_URL",  "https://aether-c7bg.vercel.app").rstrip("/")

# Render free tier cold-starts take up to 50 s; allow headroom
_HTTP_TIMEOUT = 120.0

# How long to poll for the background PB write-back to land
_BG_POLL_INTERVAL_S = 5
_BG_POLL_DEADLINE_S = 90   # covers cold-start + company research + PB round-trip


def _api(path: str) -> str:
    return urljoin(API_URL + "/", path.lstrip("/"))


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def client() -> httpx.Client:  # type: ignore[misc]
    with httpx.Client(timeout=_HTTP_TIMEOUT) as c:
        yield c


_TRIAGE_MESSAGE = (
    "Hi, we're evaluating tools to centralize scattered customer feedback and tie it "
    "to roadmap decisions. Would love a demo — we're comparing options this quarter "
    "and need something that can scale with our product team."
)


# ── Test 1: /config key presence ──────────────────────────────────────────────

def test_config_keys(client: httpx.Client) -> None:
    """GET /config: correct provider/backend config and all three keys confirmed set."""
    resp = client.get(_api("/config"))
    assert resp.status_code == 200, f"/config {resp.status_code}: {resp.text}"
    cfg = resp.json()

    assert cfg["provider"] == "openai",         f"provider={cfg['provider']!r}"
    assert cfg["crm_backend"] == "hubspot",     f"crm_backend={cfg['crm_backend']!r}"
    assert cfg["enrichment_provider"] == "pdl", f"enrichment_provider={cfg['enrichment_provider']!r}"

    # Secret presence — values are never returned, only booleans
    assert cfg["openai_key_set"] is True,    "OPENAI_API_KEY not set on Render"
    assert cfg["hubspot_token_set"] is True, "HUBSPOT_TOKEN not set on Render"
    assert cfg["pdl_key_set"] is True,       "PDL_API_KEY not set on Render"


# ── Test 2: /ready all ok ─────────────────────────────────────────────────────

def test_ready_all_ok(client: httpx.Client) -> None:
    """GET /ready: every check must be 'ok'.

    enrichment is now a real PDL probe (not the old static string compare).
    If this fails in production, checks['enrichment'] will be 'degraded'
    (PDL reachable but probe email not found — set PDL_PROBE_EMAIL) or
    'fail' (PDL_API_KEY not set on Render).
    """
    resp = client.get(_api("/ready"))
    assert resp.status_code == 200, f"/ready {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["ready"] is True, f"ready=False: {body}"

    for name, status in body.get("checks", {}).items():
        assert status == "ok", f"check '{name}' = {status!r}"


# ── Test 3: POST /triage + PB write-back ──────────────────────────────────────

def test_triage_datadoghq(client: httpx.Client) -> None:
    """POST /triage: datadoghq.com feature-ask lead.

    Assertions:
      a) A valid tier is returned immediately.
      b) The background PB write-back lands: polling the idempotency cache
         until pb_note appears with a real app.productboard.com URL.
    """
    # uuid4 for sub-second uniqueness (avoids idempotency collision in parallel runs)
    email = f"smoke+{uuid.uuid4().hex[:8]}@datadoghq.com"
    payload = {
        "email": email,
        "name": "Smoke Test",
        "company": "Datadog",
        "message": _TRIAGE_MESSAGE,
        "source": "web_form",
    }

    # ── 3a: immediate triage response ─────────────────────────────────────────
    resp = client.post(_api("/triage"), json=payload)
    assert resp.status_code == 200, f"/triage {resp.status_code}: {resp.text[:400]}"
    result = resp.json()

    tier = result.get("final_tier")
    assert tier in {"hot", "warm", "cold", "disqualified"}, (
        f"final_tier={tier!r} is not a valid tier; result={result}"
    )

    # ── 3b: poll idempotency cache until pb_note lands (or deadline) ──────────
    # _bg_enrich_and_draft() runs after response is sent; same payload re-POST
    # hits the idempotency cache and returns the progressively-enriched result.
    deadline = time.monotonic() + _BG_POLL_DEADLINE_S
    enriched = result
    while time.monotonic() < deadline:
        time.sleep(_BG_POLL_INTERVAL_S)
        r = client.post(_api("/triage"), json=payload)
        if r.status_code == 200:
            enriched = r.json()
            if enriched.get("pb_note"):
                break
    else:
        pytest.fail(
            f"pb_note never appeared after {_BG_POLL_DEADLINE_S}s. "
            "Background write-back may have failed or PRODUCTBOARD_SOURCE is not 'live'. "
            f"Last enriched result: {enriched}"
        )

    pb_note  = enriched["pb_note"]
    note_id  = pb_note.get("note_id", "")
    note_url = pb_note.get("note_url", "")

    assert note_id and note_id != "error", (
        f"pb_note.note_id={note_id!r} — PB API call failed (bad token / scope?)"
    )
    assert "app.productboard.com" in note_url, (
        f"pb_note.note_url={note_url!r} — expected app.productboard.com; "
        "PRODUCTBOARD_SOURCE may not be 'live', or token returned a fixture URL"
    )


# ── Test 4: UI smoke (Playwright, optional) ───────────────────────────────────
# Requires:  pip install pytest-playwright && playwright install chromium
# Run with:  pytest tests/smoke_live.py::test_ui_loads -v

try:
    from playwright.sync_api import Page
    from playwright.sync_api import expect as pw_expect

    @pytest.fixture(scope="module")
    def browser_context_args(browser_context_args):  # type: ignore[no-untyped-def]
        return {**browser_context_args, "base_url": UI_URL}

    def test_ui_loads(page: Page) -> None:  # type: ignore[no-untyped-def]
        """Playwright: inbound page loads, form is present, and submit is interactive."""
        page.goto(f"{UI_URL}/inbound")
        pw_expect(page.locator("nav")).to_be_visible(timeout=20_000)
        pw_expect(page.get_by_role("button", name="Submit")).to_be_visible(timeout=10_000)

except ImportError:
    def test_ui_loads() -> None:  # type: ignore[misc]
        pytest.skip(
            "playwright not installed — "
            "pip install pytest-playwright && playwright install chromium"
        )
