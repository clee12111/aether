from abc import ABC, abstractmethod


class BaseTool(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def run(self, args: dict, run_id: str = "") -> dict: ...

    def reset(self) -> None:
        """Reset any per-run accumulated state. No-op by default."""
