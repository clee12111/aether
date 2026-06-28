from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class CRMStore(ABC):
    @abstractmethod
    def lookup(self, email: str) -> dict[str, Any]:
        """Return the existing record for this email, or {"found": false}."""
        ...

    @abstractmethod
    def upsert(self, email: str, data: dict[str, Any]) -> None:
        """Insert or update a record keyed by email."""
        ...

    @abstractmethod
    def add_activity(self, email: str, activity: dict[str, Any]) -> dict[str, Any] | None:
        """Append an activity record to the contact's timeline.

        Returns the existing activity dict if this is a duplicate (dedup on
        run_id + action), or None if a new activity was recorded.
        """
        ...

    @abstractmethod
    def get_activities(self, email: str) -> list[dict[str, Any]]:
        """Return all activities for a contact, newest first."""
        ...

    def list_contacts(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return recently upserted contacts, newest first.

        Each entry should include at minimum: email, name, company, tier,
        score, route, run_id. Optional: industry, seniority, last_activity.
        """
        return []  # default empty; override in implementations

    def delete_contact(self, email: str) -> bool:
        """Delete a contact and all its activities (right-to-erasure).

        Returns True if a contact was found and deleted, False if not found.
        """
        return False  # default no-op; override in implementations

    def ping(self) -> bool:
        """Lightweight health check. Returns True if the store is reachable."""
        return True  # default optimistic; override for real checks

    def close(self) -> None:
        """Release resources. Override if the backend holds connections."""
        pass
