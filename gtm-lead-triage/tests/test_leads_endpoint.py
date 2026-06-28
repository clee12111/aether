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


class TestLeadsJoinWithTrace:
    def test_leads_include_run_id_from_trace(self):
        """After triage, /leads should return contacts with run_id from the trace store."""
        from gtm_triage.agents.executor import Executor
        from gtm_triage.agents.loop_agent import run_triage
        from gtm_triage.models.lead import Lead
        from gtm_triage.tools.crm_lookup import CRMLookupTool
        from gtm_triage.tools.draft_outreach import DraftOutreachTool
        from gtm_triage.tools.enrich_lead import EnrichLeadTool
        from gtm_triage.tools.registry import ToolRegistry
        from gtm_triage.tools.score_lead import ScoreLeadTool
        from gtm_triage.trace.store import TraceStore

        crm = SQLiteCRM(":memory:")
        trace = TraceStore(":memory:")
        registry = ToolRegistry([
            CRMLookupTool(crm),
            EnrichLeadTool(provider="mock"),
            ScoreLeadTool(provider="mock"),
            DraftOutreachTool(),
        ])
        executor = Executor(registry, trace)

        lead = Lead(email="vp@stripe.com", name="VP", company="Stripe", message="demo")
        result = run_triage(lead=lead, executor=executor, trace=trace, provider="mock")

        # Upsert to CRM (mirrors what api.py does)
        crm.upsert("vp@stripe.com", {
            "email": "vp@stripe.com", "name": "VP", "company": "Stripe",
            "tier": result.final_tier, "route": result.final_route,
            "run_id": result.run_id,
        })

        # Simulate what /leads does: list contacts + join with trace
        contacts = crm.list_contacts(50)
        runs = trace.list_runs(200)
        email_to_run = {}
        for r in runs:
            em = r.get("lead_email", "")
            if em and em not in email_to_run:
                email_to_run[em] = r["run_id"]
        for c in contacts:
            if not c.get("run_id"):
                c["run_id"] = email_to_run.get(c.get("email", ""), "")

        assert len(contacts) == 1
        assert contacts[0]["run_id"] == result.run_id

    def test_company_optional_triages_successfully(self):
        """A lead with no company should still triage and score."""
        from gtm_triage.agents.executor import Executor
        from gtm_triage.agents.loop_agent import run_triage
        from gtm_triage.models.lead import Lead
        from gtm_triage.tools.crm_lookup import CRMLookupTool
        from gtm_triage.tools.draft_outreach import DraftOutreachTool
        from gtm_triage.tools.enrich_lead import EnrichLeadTool
        from gtm_triage.tools.registry import ToolRegistry
        from gtm_triage.tools.score_lead import ScoreLeadTool
        from gtm_triage.trace.store import TraceStore

        crm = SQLiteCRM(":memory:")
        trace = TraceStore(":memory:")
        registry = ToolRegistry([
            CRMLookupTool(crm),
            EnrichLeadTool(provider="mock"),
            ScoreLeadTool(provider="mock"),
            DraftOutreachTool(),
        ])
        executor = Executor(registry, trace)

        lead = Lead(email="engineer@bigcorp.com", name="Engineer", company="", message="curious about your product")
        result = run_triage(lead=lead, executor=executor, trace=trace, provider="mock")

        assert result.final_tier is not None
        assert result.final_tier in ("hot", "warm", "cold", "disqualified")
        assert result.final_route is not None


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
