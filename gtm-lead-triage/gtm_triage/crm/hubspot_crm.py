"""HubSpot-backed CRM store using the v3 REST API.

Auth: Bearer token from a HubSpot Private App (env HUBSPOT_TOKEN).
Required scopes: crm.objects.contacts.read, crm.objects.contacts.write,
                 crm.schemas.contacts.write.

Activities are stored as lines in a custom multiline-text property
``gtm_activity_log`` on the contact (free accounts lack Notes scope).
Dedup: a line is only appended if the exact ``[run_id] action`` string is
not already present.

Custom properties needed on the Contacts object:
  gtm_tier, gtm_score, gtm_route, gtm_industry, gtm_seniority (single-line text)
  gtm_activity_log (multiline text)
Create them via ``scripts/hubspot_smoke.py --setup`` or manually in Settings >
Properties.
"""

from __future__ import annotations

from typing import Any

import httpx

import logging

from gtm_triage.crm.base import CRMStore

logger = logging.getLogger(__name__)

_BASE = "https://api.hubapi.com"

# Our field → HubSpot property mapping
_FIELD_MAP: dict[str, str] = {
    "email": "email",
    "name": "firstname",  # we split in upsert
    "company": "company",
    "tier": "gtm_tier",
    "route": "gtm_route",
    "industry": "gtm_industry",
    "seniority": "gtm_seniority",
}

# Properties we always fetch
_READ_PROPS = [
    "email", "firstname", "lastname", "company",
    "gtm_tier", "gtm_score", "gtm_route",
    "gtm_industry", "gtm_seniority", "gtm_activity_log",
]


class HubSpotCRM(CRMStore):
    """CRMStore backed by HubSpot v3 REST API via httpx."""

    def __init__(self, token: str, *, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(
            base_url=_BASE,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=15.0,
        )
        self._owns_client = client is None

    # ── Interface methods ────────────────────────────────────────────────

    def lookup(self, email: str) -> dict[str, Any]:
        contact = self._search_by_email(email)
        if contact is None:
            return {"found": False}
        props = contact.get("properties", {})
        return {
            "found": True,
            "hubspot_id": contact["id"],
            "email": props.get("email", ""),
            "name": f"{props.get('firstname', '')} {props.get('lastname', '')}".strip(),
            "company": props.get("company", ""),
            "tier": props.get("gtm_tier", ""),
            "score": props.get("gtm_score", ""),
            "route": props.get("gtm_route", ""),
            "industry": props.get("gtm_industry", ""),
            "seniority": props.get("gtm_seniority", ""),
        }

    def upsert(self, email: str, data: dict[str, Any]) -> None:
        props = self._map_to_hubspot_props(data)
        contact = self._search_by_email(email)
        if contact is not None:
            cid = contact["id"]
            resp = self._client.patch(f"/crm/v3/objects/contacts/{cid}", json={"properties": props})
            if not resp.is_success:
                logger.error(
                    "HubSpot PATCH /contacts/%s failed: %d %s",
                    cid, resp.status_code, resp.text,
                )
            resp.raise_for_status()
        else:
            props["email"] = email
            resp = self._client.post("/crm/v3/objects/contacts", json={"properties": props})
            if not resp.is_success:
                logger.error(
                    "HubSpot POST /contacts failed: %d %s | props=%s",
                    resp.status_code, resp.text, props,
                )
            resp.raise_for_status()

    def add_activity(self, email: str, activity: dict[str, Any]) -> dict[str, Any] | None:
        run_id = activity.get("run_id", "")
        action = activity.get("action", "")
        line = f"[{run_id}] {action}"

        contact = self._search_by_email(email)
        if contact is None:
            return None  # no contact to attach to

        cid = contact["id"]
        existing_log = (contact.get("properties") or {}).get("gtm_activity_log") or ""

        # Dedup: check if this exact line is already present
        if line in existing_log:
            return {"activity": activity, "status": "already_recorded"}

        # Append line
        updated = f"{existing_log}\n{line}".strip()
        resp = self._client.patch(
            f"/crm/v3/objects/contacts/{cid}",
            json={"properties": {"gtm_activity_log": updated}},
        )
        if not resp.is_success:
            logger.error(
                "HubSpot PATCH activity for %s failed: %d %s",
                cid, resp.status_code, resp.text,
            )
        resp.raise_for_status()
        return None  # new activity recorded

    def get_activities(self, email: str) -> list[dict[str, Any]]:
        contact = self._search_by_email(email)
        if contact is None:
            return []
        log = (contact.get("properties") or {}).get("gtm_activity_log") or ""
        activities: list[dict[str, Any]] = []
        for line in log.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            # Parse "[run_id] action" format
            if line.startswith("[") and "] " in line:
                bracket_end = line.index("] ")
                run_id = line[1:bracket_end]
                action = line[bracket_end + 2:]
                activities.append({"run_id": run_id, "action": action})
            else:
                activities.append({"action": line})
        # Newest first (last appended = newest)
        activities.reverse()
        return [{"activity": a} for a in activities]

    def delete_contact(self, email: str) -> bool:
        """Archive a HubSpot contact by email (right-to-erasure)."""
        contact = self._search_by_email(email)
        if contact is None:
            return False
        cid = contact["id"]
        resp = self._client.delete(f"/crm/v3/objects/contacts/{cid}")
        if not resp.is_success:
            logger.warning("HubSpot DELETE /contacts/%s failed: %d %s", cid, resp.status_code, resp.text)
            return False
        return True

    def list_contacts(self, limit: int = 50) -> list[dict[str, Any]]:
        """List recently modified contacts that have a gtm_tier set."""
        body = {
            "filterGroups": [{
                "filters": [{
                    "propertyName": "gtm_tier",
                    "operator": "HAS_PROPERTY",
                }]
            }],
            "properties": _READ_PROPS,
            "sorts": [{"propertyName": "lastmodifieddate", "direction": "DESCENDING"}],
            "limit": min(limit, 100),
        }
        resp = self._client.post("/crm/v3/objects/contacts/search", json=body)
        if resp.status_code != 200:
            logger.warning("HubSpot list_contacts search failed: %d %s", resp.status_code, resp.text)
            return []

        results = []
        for contact in resp.json().get("results", []):
            props = contact.get("properties", {})
            name = f"{props.get('firstname', '')} {props.get('lastname', '')}".strip()
            results.append({
                "email": props.get("email", ""),
                "name": name,
                "company": props.get("company", ""),
                "tier": props.get("gtm_tier", ""),
                "score": props.get("gtm_score", ""),
                "route": props.get("gtm_route", ""),
                "industry": props.get("gtm_industry", ""),
                "seniority": props.get("gtm_seniority", ""),
            })
        return results

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    # ── Internal helpers ─────────────────────────────────────────────────

    def _search_by_email(self, email: str) -> dict[str, Any] | None:
        body = {
            "filterGroups": [{
                "filters": [{
                    "propertyName": "email",
                    "operator": "EQ",
                    "value": email,
                }]
            }],
            "properties": _READ_PROPS,
        }
        resp = self._client.post("/crm/v3/objects/contacts/search", json=body)
        if resp.status_code != 200:
            return None
        data = resp.json()
        results = data.get("results", [])
        return results[0] if results else None

    @staticmethod
    def _map_to_hubspot_props(data: dict[str, Any]) -> dict[str, str]:
        props: dict[str, str] = {}
        for our_key, hs_key in _FIELD_MAP.items():
            val = data.get(our_key)
            if val is None:
                continue
            if our_key == "name":
                parts = str(val).split(" ", 1)
                props["firstname"] = parts[0]
                props["lastname"] = parts[1] if len(parts) > 1 else ""
            else:
                props[hs_key] = str(val)
        # Score is special — stored as gtm_score
        if "score" in data or "points" in data:
            props["gtm_score"] = str(data.get("score") or data.get("points", ""))
        return props
