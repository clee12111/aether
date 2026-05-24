# aether/tools/answer_from_context.py

"""
AnswerFromContextTool — synthesize a grounded answer from retrieved evidence.

DESIGN NOTE — the deliberate LLM exception
-------------------------------------------
The Aether executor was built zero-LLM by design: load_data, run_sql,
flag_item, and write_report are fully deterministic.  That principle still
holds for data operations.  This tool is the single, explicitly isolated
exception:

    Data operations are deterministic (load_data / run_sql / flag_item /
    write_report).  Synthesis over retrieved text is an explicit, auditable
    LLM step — clearly separated, clearly labeled.

The distinction matters:
- Deterministic tools produce verifiable, reproducible results.
- Synthesis produces *reasoned* output that must be audited differently:
  grounded/insufficient_context flags, token counts in the observation,
  and the critic can inspect both.

This is NOT a general "ask the LLM anything" escape hatch.  It takes
evidence IN and produces an answer grounded to that evidence.  The system
prompt forbids fabrication and requires the model to explicitly declare
when the evidence is insufficient — directly addressing the
fabrication-from-null failure mode found in earlier eval runs.
"""

from __future__ import annotations

import logging

from aether.agents.llm_client import chat
from aether.config import Settings
from aether.tools.base import BaseTool

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a grounded answer synthesizer. Your ONLY job is to answer a question \
using the context passages provided. Nothing else.

RULES — follow exactly:
1. Answer using ONLY information present in the context. Do not use outside knowledge.
2. Cite which passage(s) support the answer (e.g. "page 3", "chunk 2").
3. If the context does NOT contain enough information to answer the question,
   begin your response with the exact token INSUFFICIENT_CONTEXT: and explain
   what is missing. Do not guess, infer, or fill in from memory.
4. Never fabricate facts, statistics, names, dates, or claims not in the context.
5. Be concise. One clear answer paragraph, then a citation line.

Output format (plain text, no JSON):
  <your answer, or INSUFFICIENT_CONTEXT: <reason>>
  Source: <brief citation, e.g. "page 4, paragraph 2">
"""


class AnswerFromContextTool(BaseTool):
    """Synthesizes a grounded answer to a question from retrieved evidence chunks.

    The deliberate, isolated LLM-calling exception in an otherwise deterministic
    executor.  See module docstring for the full design rationale.

    Takes a question + evidence text, makes ONE LLM call (same provider routing
    as the planner — openai/gpt-5.4-mini by default) to produce a grounded
    answer, and returns it with explicit grounded/insufficient_context flags so
    the critic and trace store can audit the synthesis.

    Args:
        settings: Application settings for provider/model routing.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def name(self) -> str:
        return "answer_from_context"

    def run(self, args: dict) -> dict:
        """Synthesize an answer from provided evidence.

        Expected args:
            question (str): The question to answer.
            context  (str | list[str]): Retrieved chunk texts.
                     A list is joined with '---' separators before the call.

        Returns:
            {
                "answer":               str,   # synthesized answer text
                "grounded":             bool,  # True if context was sufficient
                "insufficient_context": bool,  # True if context lacked the answer
                "input_tokens":         int,   # LLM input token count
                "output_tokens":        int,   # LLM output token count
            }
        """
        question = args.get("question", "").strip()
        if not question:
            logger.warning("answer_from_context called with empty question")
            return {
                "answer": "",
                "grounded": False,
                "insufficient_context": True,
                "error": "answer_from_context requires a non-empty 'question' argument",
                "input_tokens": 0,
                "output_tokens": 0,
            }

        raw_context = args.get("context", "")
        if isinstance(raw_context, list):
            context_text = "\n---\n".join(str(c) for c in raw_context if c)
        else:
            context_text = str(raw_context).strip()

        if not context_text:
            logger.warning("answer_from_context called with empty context")
            return {
                "answer": "INSUFFICIENT_CONTEXT: No evidence chunks were provided.",
                "grounded": False,
                "insufficient_context": True,
                "input_tokens": 0,
                "output_tokens": 0,
            }

        user_prompt = f"CONTEXT:\n{context_text}\n\nQUESTION: {question}"

        # Provider routing mirrors the planner — same frontier model, same logic.
        provider = self._settings.planner_provider
        if provider == "ollama":
            model = self._settings.planner_model_local
        elif provider == "openai":
            model = self._settings.planner_model_openai
        else:
            model = self._settings.planner_model

        logger.info(
            "answer_from_context: synthesizing (provider=%s model=%s "
            "question_len=%d context_len=%d)",
            provider, model, len(question), len(context_text),
        )

        result = chat(
            provider=provider,
            model=model,
            system=_SYSTEM_PROMPT,
            user=user_prompt,
            settings=self._settings,
            max_tokens=1024,
        )

        answer_text = result.text.strip()
        insufficient = answer_text.upper().startswith("INSUFFICIENT_CONTEXT")

        logger.info(
            "answer_from_context: complete — grounded=%s insufficient=%s "
            "in_tok=%d out_tok=%d",
            not insufficient, insufficient,
            result.input_tokens, result.output_tokens,
        )

        return {
            "answer": answer_text,
            "grounded": not insufficient,
            "insufficient_context": insufficient,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
        }
