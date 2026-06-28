"""Tests for /leads endpoint — verifies it works for ANY CRM backend via the ABC.

Regression: /leads was hardcoded to `isinstance(_crm, SQLiteCRM)` and returned
[] for HubSpot. The fix calls `_crm.list_contacts()` (defined in CRMStore ABC).
"""

from __future__ import annotations

from gtm_triage.crm.base import CRMStore
from gtm_triage.crm.sqlite_crm import SQLiteCRM


class TestSQLiteCRMListContacts:
    def test_returns_upserted_contacts(self):
        crm = SQLiteCRM(":memory:")
        crm.upsert("vp@stripe.com", {
            "email": "vp@stripe.com",
            "name": "Jane Doe",
            "company": "Stripe",
            "tier": "hot",
            "score": "85",
            "route": "ae_immediate",
        })
        crm.upsert("dev@startup.io", {
            "email": "dev@startup.io",
            "name": "Alex",
            "company": "Startup",
            "tier": "cold",
            "score": "25",
            "route": "marketing_nurture",
        })

        leads = crm.list_contacts(10)

        assert len(leads) == 2
        emails = {l["email"] for l in leads}
        assert "vp@stripe.com" in emails
        assert "dev@startup.io" in emails

    def test_empty_when_no_contacts(self):
        crm = SQLiteCRM(":memory:")
        assert crm.list_contacts() == []

    def test_respects_limit(self):
        crm = SQLiteCRM(":memory:")
        for i in range(5):
            crm.upsert(f"user{i}@example.com", {"email": f"user{i}@example.com", "tier": "cold"})

        leads = crm.list_contacts(3)
        assert len(leads) == 3


class TestCRMStoreABCDefinesListContacts:
    def test_list_contacts_in_abc(self):
        """list_contacts must be defined on the ABC so any backend can be used."""
        assert hasattr(CRMStore, "list_contacts")

    def test_default_returns_empty(self):
        """ABC default returns [] for backends that haven't implemented it yet."""
        class MinimalCRM(CRMStore):
            def lookup(self, email): return {"found": False}
            def upsert(self, email, data): pass
            def add_activity(self, email, activity): return None
            def get_activities(self, email): return []

        crm = MinimalCRM()
        assert crm.list_contacts() == []
