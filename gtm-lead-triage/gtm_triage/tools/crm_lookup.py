from __future__ import annotations

from gtm_triage.crm.base import CRMStore
from gtm_triage.tools.base import BaseTool


class CRMLookupTool(BaseTool):
    def __init__(self, crm: CRMStore) -> None:
        self._crm = crm

    @property
    def name(self) -> str:
        return "crm_lookup"

    def run(self, args: dict, run_id: str = "") -> dict:
        email = args.get("email", "")
        if not email:
            return {"found": False, "error": "email is required"}
        return self._crm.lookup(email)
