"""Motion ABC — the seam between the generic RAO loop and domain-specific
triage logic (inbound signup, outbound list, etc.).

Each Motion supplies:
  - the system prompt and tool list for the LLM
  - pre-loop signal computation and short-circuit decisions
  - context injection into tool args
  - post-tool branching (CRM hit, confidence gate, DIG_DEEPER, result capture)
  - default trace path label
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from gtm_triage.models.action import LoopStep, TriageResult
from gtm_triage.models.signal import Signal
from gtm_triage.trace.store import TraceStore


class Motion(ABC):
    """Abstract base for a triage motion."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier for this motion (e.g. 'inbound')."""

    @property
    @abstractmethod
    def input_model(self) -> type:
        """The Pydantic model this motion expects (e.g. Lead)."""

    @abstractmethod
    def system_prompt(self) -> str:
        """Return the full system prompt for the LLM."""

    @abstractmethod
    def tool_names(self) -> list[str]:
        """Ordered list of tool names this motion may use."""

    @abstractmethod
    def compute_pre_signals(self, signal: Signal) -> dict[str, Any]:
        """Cheap, deterministic pre-loop checks on the input signal."""

    @abstractmethod
    def pre_loop_result(
        self,
        signal: Signal,
        pre_signals: dict[str, Any],
    ) -> TriageResult | None:
        """Return a TriageResult to short-circuit the loop, or None to continue."""

    @abstractmethod
    def inject_context(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        signal: Signal,
        steps: list[LoopStep],
    ) -> dict[str, Any]:
        """Inject known context into tool args before dispatch."""

    @abstractmethod
    def post_tool(
        self,
        tool_name: str,
        output: dict[str, Any] | None,
        signal: Signal,
        steps: list[LoopStep],
        pre_signals: dict[str, Any],
        trace_path: str,
        run_id: str,
        trace: TraceStore,
        result: TriageResult,
    ) -> tuple[dict[str, Any] | None, str]:
        """Post-tool branching. Returns (possibly-modified output, updated trace_path)."""

    @abstractmethod
    def default_trace_path(self) -> str:
        """The trace path label when no branching condition fires."""
