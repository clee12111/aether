"""HubSpot CRM smoke test — run with YOUR token to verify the integration.

Usage:
    # Create custom properties first (one-time setup):
    HUBSPOT_TOKEN=pat-xxx python -m scripts.hubspot_smoke --setup

    # Then run the smoke test:
    HUBSPOT_TOKEN=pat-xxx python -m scripts.hubspot_smoke

This creates/updates a test contact, logs an activity, and reads it back.
"""

from __future__ import annotations

import os
import sys

TEST_EMAIL = "gtm-smoke-test@aether-gtm-demo.com"


def _get_crm():
    from gtm_triage.crm.hubspot_crm import HubSpotCRM

    token = os.environ.get("HUBSPOT_TOKEN", "")
    if not token:
        print("ERROR: Set HUBSPOT_TOKEN env var (Private App token)")
        sys.exit(1)
    return HubSpotCRM(token)


def setup_properties():
    """Create the custom contact properties needed by the GTM agent."""
    import httpx

    token = os.environ.get("HUBSPOT_TOKEN", "")
    if not token:
        print("ERROR: Set HUBSPOT_TOKEN env var")
        sys.exit(1)

    client = httpx.Client(
        base_url="https://api.hubapi.com",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        timeout=15.0,
    )

    props = [
        ("gtm_tier", "GTM Tier", "string"),
        ("gtm_score", "GTM Score", "string"),
        ("gtm_route", "GTM Route", "string"),
        ("gtm_industry", "GTM Industry", "string"),
        ("gtm_seniority", "GTM Seniority", "string"),
        ("gtm_activity_log", "GTM Activity Log", "string"),
    ]

    print("\n  Creating custom contact properties...\n")
    for name, label, field_type in props:
        body = {
            "name": name,
            "label": label,
            "type": "string",
            "fieldType": "textarea" if name == "gtm_activity_log" else "text",
            "groupName": "contactinformation",
        }
        resp = client.post("/crm/v3/properties/contacts", json=body)
        if resp.status_code == 201:
            print(f"    CREATED: {name}")
        elif resp.status_code == 409:
            print(f"    EXISTS:  {name}")
        else:
            print(f"    ERROR:   {name} -> {resp.status_code}: {resp.text}")

    client.close()
    print("\n  Done. You can now run the smoke test.\n")


def smoke_test():
    import time

    crm = _get_crm()

    print(f"\n{'='*60}")
    print("  HubSpot CRM Smoke Test")
    print(f"{'='*60}\n")

    # 1. Lookup (may or may not exist)
    print(f"  [1] lookup({TEST_EMAIL})...")
    record = crm.lookup(TEST_EMAIL)
    print(f"      found={record.get('found')}")
    if record.get("found"):
        print(f"      hubspot_id={record.get('hubspot_id')}")

    # 2. Upsert
    print(f"\n  [2] upsert({TEST_EMAIL}, tier=hot, score=80)...")
    crm.upsert(TEST_EMAIL, {
        "email": TEST_EMAIL,
        "name": "Smoke Test User",
        "company": "Aether Demo Corp",
        "tier": "hot",
        "score": "80",
        "route": "ae_immediate",
        "industry": "fintech",
        "seniority": "vp",
    })
    print("      OK")

    # 3. Verify upsert (HubSpot search has eventual consistency — wait up to 15s)
    print(f"\n  [3] lookup({TEST_EMAIL}) — verify upsert (waiting for search index)...")
    record = {"found": False}
    for attempt in range(5):
        time.sleep(3)
        record = crm.lookup(TEST_EMAIL)
        if record.get("found"):
            break
        print(f"      attempt {attempt + 1}/5 — not indexed yet, retrying...")
    print(f"      found={record.get('found')}, tier={record.get('tier')}, "
          f"score={record.get('score')}, name={record.get('name')}")

    # 4. Add activity
    print(f"\n  [4] add_activity — 'notified AE for immediate follow-up'...")
    result = crm.add_activity(TEST_EMAIL, {
        "run_id": "smoke-run-001",
        "action": "notified AE for immediate follow-up",
    })
    status = "already_recorded" if result else "recorded"
    print(f"      status={status}")

    # 5. Add same activity again (dedup test)
    print(f"\n  [5] add_activity — SAME again (dedup test)...")
    result2 = crm.add_activity(TEST_EMAIL, {
        "run_id": "smoke-run-001",
        "action": "notified AE for immediate follow-up",
    })
    status2 = "already_recorded" if result2 else "recorded"
    print(f"      status={status2} (should be already_recorded)")

    # 6. Get activities
    print(f"\n  [6] get_activities({TEST_EMAIL})...")
    activities = crm.get_activities(TEST_EMAIL)
    print(f"      count={len(activities)}")
    for a in activities[:5]:
        print(f"        - {a.get('activity', {}).get('action', '?')}")

    crm.close()

    print(f"\n{'='*60}")
    print("  Smoke test complete.")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    if "--setup" in sys.argv:
        setup_properties()
    else:
        smoke_test()
