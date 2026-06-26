"""
Token usage audit: single GPT-5.4-mini call with realistic prompt structure.

Replicates the exact message shape used by aether/agents/llm_client.py:
  - system message (loop agent system prompt)
  - user message (5 chunks × ~512 tokens each + query)

Prints full usage breakdown and cost at GPT-5 Mini pricing.
"""

import os
import sys

# Allow running from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openai import OpenAI

# ── Config ────────────────────────────────────────────────────────────────────
MODEL = "gpt-5.4-mini"
INPUT_PRICE_PER_M = 0.25   # $/M input tokens
OUTPUT_PRICE_PER_M = 2.00  # $/M output tokens
CACHED_PRICE_PER_M = 0.025 # $/M cached input tokens (90% discount)

# ── Build a realistic system prompt (matches loop_agent.py) ───────────────────
SYSTEM_PROMPT = """You are an autonomous agent. You reason about a goal, pick ONE tool to call, observe the result, then decide the next tool. You continue until the goal is fully satisfied.

RULES:
- Output a single AgentAction as strict JSON. No markdown fences. No extra text.
- Pick exactly one tool per response.
- Set is_final=true ONLY when the goal is completely satisfied.
- Do not repeat a tool call with the same arguments already used.

AVAILABLE TOOLS (use exact tool names and arg keys):

load_data
  {"file_path": "<path or filename>", "table_name": "<name for SQL registry>"}

run_sql
  {"sql": "<DuckDB SQL query>"}

flag_item
  {"item_id": "<identifier>", "reason": "<why flagged>", "severity": "low|medium|high"}

write_report
  {"title": "<filename>", "format": "json", "results": {<findings dict>}}

retrieve_context
  {"query": "<search string>", "top_k": 5}

answer_from_context
  {"question": "<question>", "context": ["<chunk1>", "<chunk2>", ...]}

render_visual
  {"title": "<chart title>", "x_field": "<field>", "y_field": "<field>", "data": [...], "source_step": "<step>"}

AgentAction JSON schema:
{"reasoning": str, "tool": str, "tool_args": dict, "is_final": bool}
"""

# ── Build dummy chunks (~512 tokens each) ─────────────────────────────────────
# "legal contract clause text " is ~4 tokens per repetition
# 512 tokens ≈ 128 repetitions of 4-token phrase
FILLER_PHRASE = "legal contract clause text providing detailed terms and conditions for the agreement between parties "
# ~20 tokens per phrase, so 26 reps ≈ 520 tokens
CHUNK_TEXT = (FILLER_PHRASE * 26).strip()

chunks = [f"[Chunk {i+1}]: {CHUNK_TEXT}" for i in range(5)]

QUERY = "What is the total revenue for Q3 2024 across all business segments, and which segment had the highest growth rate year-over-year?"

USER_MESSAGE = f"""GOAL: {QUERY}

RETRIEVED CONTEXT:
{chr(10).join(chunks)}

PRIOR STEPS: (none — this is step 1)

Respond with a single AgentAction JSON."""

# ── Make the call ─────────────────────────────────────────────────────────────
def main():
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        # Try loading from .env
        env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
        if os.path.exists(env_path):
            for line in open(env_path):
                line = line.strip()
                if line.startswith("OPENAI_API_KEY="):
                    api_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    if not api_key:
        print("ERROR: OPENAI_API_KEY not found in environment or .env")
        sys.exit(1)

    client = OpenAI(api_key=api_key)

    print("=" * 70)
    print(f"TOKEN AUDIT — Model: {MODEL}")
    print(f"Prompt structure: system + 5 chunks (~512 tok each) + query")
    print("=" * 70)
    print()

    resp = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_MESSAGE},
        ],
        max_completion_tokens=2048,
        temperature=0,
    )

    usage = resp.usage
    print("─── Raw usage object ───")
    print(f"  prompt_tokens:      {usage.prompt_tokens}")
    print(f"  completion_tokens:  {usage.completion_tokens}")
    print(f"  total_tokens:       {usage.total_tokens}")

    # Prompt token details
    details = getattr(usage, "prompt_tokens_details", None)
    cached = 0
    if details:
        cached = getattr(details, "cached_tokens", 0) or 0
        print(f"  prompt_tokens_details:")
        print(f"    cached_tokens:  {cached}")
        audio = getattr(details, "audio_tokens", None)
        if audio:
            print(f"    audio_tokens:   {audio}")

    # Completion token details
    comp_details = getattr(usage, "completion_tokens_details", None)
    if comp_details:
        reasoning = getattr(comp_details, "reasoning_tokens", 0) or 0
        if reasoning:
            print(f"  completion_tokens_details:")
            print(f"    reasoning_tokens: {reasoning}")

    print()

    # ── Cost calculation ──────────────────────────────────────────────────────
    uncached_input = usage.prompt_tokens - cached
    input_cost = (uncached_input / 1_000_000) * INPUT_PRICE_PER_M
    cached_cost = (cached / 1_000_000) * CACHED_PRICE_PER_M
    output_cost = (usage.completion_tokens / 1_000_000) * OUTPUT_PRICE_PER_M
    total_cost = input_cost + cached_cost + output_cost

    print("─── Cost breakdown (single call) ───")
    print(f"  Uncached input:  {uncached_input:,} tokens × ${INPUT_PRICE_PER_M}/M = ${input_cost:.6f}")
    print(f"  Cached input:    {cached:,} tokens × ${CACHED_PRICE_PER_M}/M = ${cached_cost:.6f}")
    print(f"  Output:          {usage.completion_tokens:,} tokens × ${OUTPUT_PRICE_PER_M}/M = ${output_cost:.6f}")
    print(f"  ─────────────────────────────────────────────")
    print(f"  TOTAL per query: ${total_cost:.6f}")
    print()

    # ── Extrapolation ─────────────────────────────────────────────────────────
    print("─── Extrapolation ───")
    print(f"  200 queries:   ${total_cost * 200:.4f}")
    print(f"  6,858 queries: ${total_cost * 6858:.4f}")
    print()

    # ── Extrapolation with full caching (best case) ───────────────────────────
    # If all input tokens were cached on subsequent calls
    full_cached_cost = (usage.prompt_tokens / 1_000_000) * CACHED_PRICE_PER_M + output_cost
    print("─── Best case (100% cache hit on repeat calls) ───")
    print(f"  Per query:     ${full_cached_cost:.6f}")
    print(f"  200 queries:   ${full_cached_cost * 200:.4f}")
    print(f"  6,858 queries: ${full_cached_cost * 6858:.4f}")
    print()

    # ── Model response (for sanity check) ─────────────────────────────────────
    print("─── Model response (first 300 chars) ───")
    print(resp.choices[0].message.content[:300])
    print()


if __name__ == "__main__":
    main()
