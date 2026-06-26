"""Simulate an inbound lead flowing through the full stack.

Tries n8n webhook first (the real orchestrated path). If n8n is unreachable,
falls back to calling the triage API directly and says so.

Usage:
    cd gtm-lead-triage
    python -m scripts.simulate_inbound
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

N8N_WEBHOOK = "http://localhost:5678/webhook/inbound"
API_BASE = "http://localhost:8000"

SAMPLE_LEAD = {
    "email": "demo.lead@acmefintech.com",
    "name": "Demo Lead, VP of Product",
    "company": "Acme Fintech International",
    "message": "We'd like to schedule a demo for our product team. Urgent need.",
    "source": "inbound_form",
}


def _post_json(url: str, data: dict, timeout: int = 30) -> dict | None:
    body = json.dumps(data).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, ConnectionError, TimeoutError):
        return None


def _get_json(url: str, timeout: int = 10) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, ConnectionError, TimeoutError):
        return None


def main() -> int:
    print(f"\n{'='*64}")
    print("  GTM Lead-Triage — End-to-End Simulation")
    print(f"{'='*64}\n")
    print(f"  Lead: {SAMPLE_LEAD['email']} ({SAMPLE_LEAD['name']})")
    print(f"  Message: {SAMPLE_LEAD['message']}\n")

    # Try n8n first
    print("  [1] Trying n8n webhook...", end=" ")
    n8n_result = _post_json(N8N_WEBHOOK, SAMPLE_LEAD)

    if n8n_result:
        print("OK (n8n orchestrated the flow)")
        tier = n8n_result.get("final_tier") or n8n_result.get("tier", "???")
        route = n8n_result.get("final_route") or n8n_result.get("route", "???")
        run_id = n8n_result.get("run_id", "???")
        print(f"  [2] Triage result: tier={tier}, route={route}, run_id={run_id}")
    else:
        print("UNREACHABLE")
        print("      n8n is not running. Falling back to direct API calls.\n")

        # Check API is up
        health = _get_json(f"{API_BASE}/health")
        if not health:
            print("  ERROR: Triage API is also unreachable at", API_BASE)
            print("  Start the server: python -m uvicorn gtm_triage.api:app\n")
            return 1

        # Direct triage
        print("  [1] POST /triage...", end=" ")
        triage_result = _post_json(f"{API_BASE}/triage", SAMPLE_LEAD)
        if not triage_result:
            print("FAILED")
            return 1
        tier = triage_result.get("final_tier", "???")
        route = triage_result.get("final_route", "???")
        run_id = triage_result.get("run_id", "???")
        points = triage_result.get("score", {}).get("points", "?")
        print(f"OK -> tier={tier}, route={route}, points={points}")

        # Direct deliver
        print("  [2] POST /deliver...", end=" ")
        deliver_result = _post_json(f"{API_BASE}/deliver", {
            "email": SAMPLE_LEAD["email"],
            "run_id": run_id,
            "tier": tier,
            "route": route,
        })
        if deliver_result:
            print(f"OK -> {deliver_result.get('activity_recorded', '?')}")
        else:
            print("FAILED")

    # Verify CRM has the activity
    print(f"  [3] GET /contacts/{SAMPLE_LEAD['email']}...", end=" ")
    contact = _get_json(f"{API_BASE}/contacts/{SAMPLE_LEAD['email']}")
    if contact:
        record = contact.get("record", {})
        activities = contact.get("activities", [])
        print("OK")
        print(f"      CRM record: tier={record.get('tier','?')}, route={record.get('route','?')}")
        print(f"      Activities: {len(activities)} recorded")
        for a in activities[:3]:
            act = a.get("activity", {})
            print(f"        - [{a.get('created_at','')}] {act.get('action','?')}")
    else:
        print("FAILED")

    print(f"\n  {'='*64}")
    print(f"  Loop closed: form -> {'n8n -> ' if n8n_result else ''}triage API -> deliver -> CRM")
    print(f"  {'='*64}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
