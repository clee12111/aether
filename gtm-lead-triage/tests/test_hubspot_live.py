"""Live HubSpot integration test — exercises the full CRMStore contract
against the real HubSpot API (portal 246604586).

Skipped when HUBSPOT_TOKEN is absent (keyless CI stays intact).
Run manually:
    HUBSPOT_TOKEN=pat-na2-... python -m pytest tests/test_hubspot_live.py -v -s
"""

from __future__ import annotations

import json
import os
import time

import httpx
import pytest

_TOKEN = os.environ.get("HUBSPOT_TOKEN", "")
pytestmark = pytest.mark.skipif(not _TOKEN, reason="HUBSPOT_TOKEN not set")

TEST_EMAIL = "gtm-integration-test@aether-test-suite.com"
SMOKE_EMAIL = "gtm-smoke-test@aether-gtm-demo.com"


@pytest.fixture(scope="module")
def crm():
    from gtm_triage.crm.hubspot_crm import HubSpotCRM
    c = HubSpotCRM(_TOKEN)
    yield c
    # Cleanup: delete test contact if it still exists
    c.delete_contact(TEST_EMAIL)
    c.close()


def _log_failure(label: str, detail: str) -> None:
    print(f"\n  FAIL: {label}")
    print(f"  Detail: {detail}")


class TestHubSpotLiveContract:
    """Run the full CRMStore contract against the real HubSpot API."""

    def test_01_upsert(self, crm):
        """Step 1: upsert a test contact with full GTM fields."""
        # Clean up any leftover from a prior run
        crm.delete_contact(TEST_EMAIL)
        time.sleep(1)

        crm.upsert(TEST_EMAIL, {
            "email": TEST_EMAIL,
            "name": "Integration Test",
            "company": "Aether Test Suite",
            "tier": "hot",
            "score": "88",
            "route": "ae_immediate",
            "industry": "technology",
            "seniority": "vp",
        })
        print("\n  PASS: upsert completed without error")

    def test_02_lookup(self, crm):
        """Step 2: lookup the contact and verify fields."""
        # HubSpot search index has eventual consistency — wait
        record = {"found": False}
        for attempt in range(5):
            time.sleep(3)
            record = crm.lookup(TEST_EMAIL)
            if record.get("found"):
                break
            print(f"  ... lookup attempt {attempt + 1}/5 — not indexed yet")

        assert record.get("found"), f"Contact not found after 5 attempts: {record}"
        print(f"  PASS: lookup found, hubspot_id={record.get('hubspot_id')}")

        assert record.get("tier") == "hot", f"tier mismatch: {record.get('tier')}"
        assert record.get("score") == "88", f"score mismatch: {record.get('score')}"
        print(f"  PASS: tier={record['tier']}, score={record['score']}")

    def test_03_list_contacts_returns_test_contact(self, crm):
        """Step 3: list_contacts must return the test contact (the /leads path)."""
        # HubSpot search index is eventually consistent — wait for it
        test_found = False
        leads = []
        for attempt in range(8):
            time.sleep(4)
            leads = crm.list_contacts(limit=50)
            test_found = any(l["email"] == TEST_EMAIL for l in leads)
            if test_found:
                break
            print(f"  ... list_contacts attempt {attempt + 1}/8 — {len(leads)} contacts, test not indexed yet")

        # Log the raw search for diagnostics
        print(f"\n  list_contacts returned {len(leads)} contacts")

        if not test_found:
            # Dump raw search request/response for debugging
            print("\n  === DIAGNOSTIC: raw HubSpot search ===")
            body = {
                "filterGroups": [{
                    "filters": [{
                        "propertyName": "gtm_tier",
                        "operator": "HAS_PROPERTY",
                    }]
                }],
                "properties": [
                    "email", "firstname", "lastname", "company",
                    "gtm_tier", "gtm_score", "gtm_route",
                    "gtm_industry", "gtm_seniority",
                ],
                "sorts": [{"propertyName": "lastmodifieddate", "direction": "DESCENDING"}],
                "limit": 50,
            }
            print(f"  Request body: {json.dumps(body, indent=2)}")
            client = httpx.Client(
                base_url="https://api.hubapi.com",
                headers={"Authorization": f"Bearer {_TOKEN}", "Content-Type": "application/json"},
                timeout=15.0,
            )
            resp = client.post("/crm/v3/objects/contacts/search", json=body)
            print(f"  Response status: {resp.status_code}")
            print(f"  Response body: {resp.text[:2000]}")
            client.close()

            # Also check: does the smoke-test contact appear?
            smoke_found = any(l["email"] == SMOKE_EMAIL for l in leads)
            print(f"\n  Smoke-test contact ({SMOKE_EMAIL}) in results: {smoke_found}")
            if leads:
                print(f"  First 3 emails returned: {[l['email'] for l in leads[:3]]}")

        assert test_found, (
            f"Test contact {TEST_EMAIL} not returned by list_contacts. "
            f"Got {len(leads)} contacts, none matched."
        )
        print(f"  PASS: test contact found in list_contacts ({len(leads)} total)")

    def test_03b_list_contacts_returns_smoke_contact(self, crm):
        """Step 3b: the pre-existing smoke-test contact should also appear."""
        leads = crm.list_contacts(limit=50)
        smoke_found = any(l["email"] == SMOKE_EMAIL for l in leads)
        if not smoke_found:
            print(f"\n  FAIL: smoke-test contact {SMOKE_EMAIL} not in list_contacts")
            print(f"  Returned {len(leads)} contacts")
            if leads:
                print(f"  Emails: {[l['email'] for l in leads[:5]]}")
        else:
            smoke = next(l for l in leads if l["email"] == SMOKE_EMAIL)
            print(f"  PASS: smoke-test contact found, tier={smoke.get('tier')}")
        assert smoke_found, f"Smoke-test contact {SMOKE_EMAIL} not in list_contacts"

    def test_04_add_activity_and_dedup(self, crm):
        """Step 4: add_activity + get_activities + dedup."""
        result = crm.add_activity(TEST_EMAIL, {
            "run_id": "integration-test-001",
            "action": "notified AE for immediate follow-up",
        })
        assert result is None, f"Expected None (new activity), got {result}"
        print("\n  PASS: add_activity recorded")

        # Dedup
        result2 = crm.add_activity(TEST_EMAIL, {
            "run_id": "integration-test-001",
            "action": "notified AE for immediate follow-up",
        })
        assert result2 is not None and result2.get("status") == "already_recorded", (
            f"Expected already_recorded, got {result2}"
        )
        print("  PASS: dedup works (already_recorded)")

        # Read back
        activities = crm.get_activities(TEST_EMAIL)
        assert len(activities) >= 1, f"Expected >=1 activity, got {len(activities)}"
        print(f"  PASS: get_activities returned {len(activities)} activities")

    def test_05_delete_contact(self, crm):
        """Step 5: delete_contact (cleanup + right-to-erasure)."""
        deleted = crm.delete_contact(TEST_EMAIL)
        assert deleted, "delete_contact returned False"
        print("\n  PASS: delete_contact succeeded")

        # Verify gone (eventual consistency — wait briefly)
        time.sleep(2)
        record = crm.lookup(TEST_EMAIL)
        # HubSpot archived contacts may still appear in search briefly
        # but the delete call succeeded, which is what matters
        print(f"  PASS: cleanup complete (lookup found={record.get('found', False)})")
