from __future__ import annotations

from gtm_triage.tools.base import BaseTool


class ToolRegistry:
    def __init__(self, tools: list[BaseTool]) -> None:
        self._tools: dict[str, BaseTool] = {t.name: t for t in tools}

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def reset_all(self) -> None:
        for t in self._tools.values():
            t.reset()
