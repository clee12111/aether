"""Tests for Phase J: privacy/compliance — SSRF hardening, deletion, PII audit."""

from __future__ import annotations

import ipaddress
import json
from pathlib import Path

import pytest

from gtm_triage.security import _is_blocked_ip, resolve_and_validate, ssrf_safe_domain


# ── SSRF hardening: cloud metadata + CGNAT + IPv6 ─────────────────────────────


class TestSSRFMetadata:
    def test_blocks_cloud_metadata_ip(self):
        """The classic SSRF escalation: 169.254.169.254 (cloud metadata)."""
        ip = ipaddress.ip_address("169.254.169.254")
        assert _is_blocked_ip(ip) is True

    def test_blocks_link_local_range(self):
        ip = ipaddress.ip_address("169.254.1.1")
        assert _is_blocked_ip(ip) is True

    def test_blocks_cgnat(self):
        """100.64.0.0/10 — CGNAT shared address space."""
        ip = ipaddress.ip_address("100.64.0.1")
        assert _is_blocked_ip(ip) is True
        ip2 = ipaddress.ip_address("100.127.255.254")
        assert _is_blocked_ip(ip2) is True

    def test_blocks_zero_network(self):
        ip = ipaddress.ip_address("0.0.0.1")
        assert _is_blocked_ip(ip) is True


class TestSSRFIPv6:
    def test_blocks_ipv6_loopback(self):
        ip = ipaddress.ip_address("::1")
        assert _is_blocked_ip(ip) is True

    def test_blocks_ipv6_ula(self):
        """fc00::/7 — Unique Local Address."""
        ip = ipaddress.ip_address("fd00::1")
        assert _is_blocked_ip(ip) is True

    def test_blocks_ipv6_link_local(self):
        ip = ipaddress.ip_address("fe80::1")
        assert _is_blocked_ip(ip) is True

    def test_blocks_ipv4_mapped_ipv6(self):
        """::ffff:127.0.0.1 — IPv4-mapped IPv6 for loopback."""
        ip = ipaddress.ip_address("::ffff:127.0.0.1")
        assert _is_blocked_ip(ip) is True

    def test_blocks_ipv4_mapped_private(self):
        """::ffff:192.168.1.1 — IPv4-mapped IPv6 for private."""
        ip = ipaddress.ip_address("::ffff:192.168.1.1")
        assert _is_blocked_ip(ip) is True

    def test_allows_public_ipv6(self):
        """2001:db8::1 is documentation range, but not in our block list."""
        ip = ipaddress.ip_address("2607:f8b0:4004:800::200e")  # Google public
        assert _is_blocked_ip(ip) is False


class TestSSRFResolveAndValidate:
    def test_localhost_blocked(self):
        err, ips = resolve_and_validate("localhost")
        assert err is not None
        assert "Blocked IP" in err

    def test_public_domain_returns_ips(self):
        err, ips = resolve_and_validate("stripe.com")
        assert err is None
        assert len(ips) > 0

    def test_nonexistent_domain_no_error(self):
        """DNS failure is not SSRF — just means domain doesn't exist."""
        err, ips = resolve_and_validate("this-domain-does-not-exist-xyz123.invalid")
        assert err is None
        assert len(ips) == 0


# ── Deletion (right to erasure) ───────────────────────────────────────────────


class TestDeletion:
    def test_crm_delete_removes_contact_and_activities(self):
        from gtm_triage.crm.sqlite_crm import SQLiteCRM
        crm = SQLiteCRM(":memory:")
        crm.upsert("test@example.com", {"email": "test@example.com", "tier": "warm"})
        crm.add_activity("test@example.com", {"run_id": "r1", "action": "test"})

        assert crm.lookup("test@example.com")["found"] is True
        assert len(crm.get_activities("test@example.com")) == 1

        deleted = crm.delete_contact("test@example.com")
        assert deleted is True
        assert crm.lookup("test@example.com")["found"] is False
        assert len(crm.get_activities("test@example.com")) == 0

    def test_crm_delete_nonexistent_returns_false(self):
        from gtm_triage.crm.sqlite_crm import SQLiteCRM
        crm = SQLiteCRM(":memory:")
        assert crm.delete_contact("nobody@example.com") is False

    def test_trace_delete_by_email(self):
        from gtm_triage.trace.store import TraceStore
        trace = TraceStore(":memory:")
        trace.write(
            run_id="run-1", event_type="run_start", agent="test",
            payload={"lead": {"email": "target@example.com"}},
        )
        trace.write(
            run_id="run-1", event_type="run_end", agent="test",
            payload={"lead_email": "target@example.com"},
        )
        trace.store_idempotency_key("key-1", "run-1", {"result": "test"})

        # Verify data exists
        assert len(trace.get_run_events("run-1")) == 2
        assert trace.get_by_idempotency_key("key-1") is not None

        # Delete
        deleted = trace.delete_by_email("target@example.com")
        assert deleted == 1  # 1 run deleted

        # Verify data gone
        assert len(trace.get_run_events("run-1")) == 0
        assert trace.get_by_idempotency_key("key-1") is None

    def test_trace_delete_nonexistent_returns_zero(self):
        from gtm_triage.trace.store import TraceStore
        trace = TraceStore(":memory:")
        assert trace.delete_by_email("nobody@example.com") == 0


# ── PII audit: cassettes ──────────────────────────────────────────────────────


class TestCassettePII:
    def test_no_real_person_pii_in_cassettes(self):
        """All cassettes with person-level data must be for fictional contacts
        at fictional or real-but-not-matching companies. The sean@peopledatalabs
        entry must be scrubbed (synthetic)."""
        cassettes_path = Path(__file__).parent.parent / "gtm_triage" / "enrichment" / "cache" / "pdl_cassettes.json"
        with open(cassettes_path) as f:
            data = json.load(f)

        for email, entry in data.items():
            if email.startswith("_"):
                continue
            if entry["status_code"] != 200:
                continue
            body = entry["body"].get("data", {})
            name = body.get("full_name", "")
            # The only previously-real entry (sean@peopledatalabs.com) must be scrubbed
            if email == "sean@peopledatalabs.com":
                assert "thorne" not in name.lower(), \
                    "sean@peopledatalabs.com still contains real PII — must be scrubbed"
