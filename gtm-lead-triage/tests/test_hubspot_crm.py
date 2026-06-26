"""Unit tests for HubSpotCRM against a mocked httpx client.

Verifies correct HubSpot v3 endpoints, payloads, and dedup logic
WITHOUT making live API calls.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from gtm_triage.crm.hubspot_crm import HubSpotCRM


# ── Helpers ──────────────────────────────────────────────────────────────────

def _mock_response(status: int = 200, data: dict | None = None) -> httpx.Response:
    """Build a fake httpx.Response."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status
    resp.json.return_value = data or {}
    return resp


def _contact_result(
    cid: str = "101",
    email: str = "test@acme.com",
    extra_props: dict[str, str] | None = None,
) -> dict[str, Any]:
    props = {
        "email": email,
        "firstname": "Test",
        "lastname": "User",
        "company": "Acme",
        "gtm_tier": "hot",
        "gtm_score": "80",
        "gtm_route": "ae_immediate",
        "gtm_industry": "fintech",
        "gtm_seniority": "vp",
        "gtm_activity_log": "",
    }
    if extra_props:
        props.update(extra_props)
    return {"total": 1, "results": [{"id": cid, "properties": props}]}


# ── Tests ────────────────────────────────────────────────────────────────────

class TestLookup:
    def test_found(self):
        client = MagicMock(spec=httpx.Client)
        client.post.return_value = _mock_response(200, _contact_result())
        crm = HubSpotCRM("tok", client=client)

        result = crm.lookup("test@acme.com")

        assert result["found"] is True
        assert result["hubspot_id"] == "101"
        assert result["email"] == "test@acme.com"
        assert result["name"] == "Test User"
        assert result["tier"] == "hot"

        # Verify correct endpoint
        call_args = client.post.call_args
        assert call_args[0][0] == "/crm/v3/objects/contacts/search"
        body = call_args[1]["json"]
        assert body["filterGroups"][0]["filters"][0]["value"] == "test@acme.com"

    def test_not_found(self):
        client = MagicMock(spec=httpx.Client)
        client.post.return_value = _mock_response(200, {"total": 0, "results": []})
        crm = HubSpotCRM("tok", client=client)

        result = crm.lookup("nobody@example.com")
        assert result == {"found": False}

    def test_api_error_returns_not_found(self):
        client = MagicMock(spec=httpx.Client)
        client.post.return_value = _mock_response(500)
        crm = HubSpotCRM("tok", client=client)

        result = crm.lookup("test@acme.com")
        assert result == {"found": False}


class TestUpsert:
    def test_update_existing(self):
        client = MagicMock(spec=httpx.Client)
        client.post.return_value = _mock_response(200, _contact_result(cid="42"))
        client.patch.return_value = _mock_response(200)
        crm = HubSpotCRM("tok", client=client)

        crm.upsert("test@acme.com", {"email": "test@acme.com", "name": "New Name", "tier": "warm"})

        # Search was called
        assert client.post.call_count == 1
        # PATCH with correct id
        patch_args = client.patch.call_args
        assert "/crm/v3/objects/contacts/42" in patch_args[0][0]
        props = patch_args[1]["json"]["properties"]
        assert props["firstname"] == "New"
        assert props["lastname"] == "Name"
        assert props["gtm_tier"] == "warm"

    def test_create_new(self):
        client = MagicMock(spec=httpx.Client)
        # Search returns empty
        search_resp = _mock_response(200, {"total": 0, "results": []})
        create_resp = _mock_response(201)
        client.post.side_effect = [search_resp, create_resp]
        crm = HubSpotCRM("tok", client=client)

        crm.upsert("new@acme.com", {"email": "new@acme.com", "name": "New Lead", "company": "Acme"})

        # Two POSTs: search + create
        assert client.post.call_count == 2
        create_call = client.post.call_args_list[1]
        assert create_call[0][0] == "/crm/v3/objects/contacts"
        props = create_call[1]["json"]["properties"]
        assert props["email"] == "new@acme.com"
        assert props["firstname"] == "New"
        assert props["company"] == "Acme"


class TestAddActivity:
    def test_new_activity_appended(self):
        client = MagicMock(spec=httpx.Client)
        client.post.return_value = _mock_response(200, _contact_result(
            extra_props={"gtm_activity_log": ""}
        ))
        client.patch.return_value = _mock_response(200)
        crm = HubSpotCRM("tok", client=client)

        result = crm.add_activity("test@acme.com", {
            "run_id": "run-1", "action": "notified AE"
        })

        assert result is None  # new activity
        patch_args = client.patch.call_args
        props = patch_args[1]["json"]["properties"]
        assert "[run-1] notified AE" in props["gtm_activity_log"]

    def test_duplicate_activity_deduped(self):
        client = MagicMock(spec=httpx.Client)
        client.post.return_value = _mock_response(200, _contact_result(
            extra_props={"gtm_activity_log": "[run-1] notified AE"}
        ))
        crm = HubSpotCRM("tok", client=client)

        result = crm.add_activity("test@acme.com", {
            "run_id": "run-1", "action": "notified AE"
        })

        assert result is not None  # duplicate
        assert result["status"] == "already_recorded"
        # No PATCH should have been called (dedup skipped it)
        client.patch.assert_not_called()

    def test_no_contact_returns_none(self):
        client = MagicMock(spec=httpx.Client)
        client.post.return_value = _mock_response(200, {"total": 0, "results": []})
        crm = HubSpotCRM("tok", client=client)

        result = crm.add_activity("nobody@example.com", {
            "run_id": "run-1", "action": "test"
        })
        assert result is None


class TestGetActivities:
    def test_parses_activity_log(self):
        client = MagicMock(spec=httpx.Client)
        client.post.return_value = _mock_response(200, _contact_result(
            extra_props={"gtm_activity_log": "[run-1] notified AE\n[run-2] added to nurture"}
        ))
        crm = HubSpotCRM("tok", client=client)

        activities = crm.get_activities("test@acme.com")

        assert len(activities) == 2
        # Newest first (reversed)
        assert activities[0]["activity"]["run_id"] == "run-2"
        assert activities[1]["activity"]["run_id"] == "run-1"

    def test_empty_log(self):
        client = MagicMock(spec=httpx.Client)
        client.post.return_value = _mock_response(200, _contact_result(
            extra_props={"gtm_activity_log": ""}
        ))
        crm = HubSpotCRM("tok", client=client)

        activities = crm.get_activities("test@acme.com")
        assert activities == []
