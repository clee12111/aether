# aether/tools/base.py

from abc import ABC, abstractmethod


class BaseTool(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def run(self, args: dict) -> dict: ...
