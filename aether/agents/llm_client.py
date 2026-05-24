from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ChatResult:
    text: str
    input_tokens: int
    output_tokens: int


def chat(
    *,
    provider: str,
    model: str,
    system: str,
    user: str,
    settings,
    max_tokens: int = 2048,
) -> ChatResult:
    """Provider-agnostic single-turn chat. Returns ChatResult with text and token counts.

    provider == 'ollama'    -> OpenAI-compatible call to settings.ollama_base_url
    provider == 'anthropic' -> anthropic SDK call
    """
    if provider == "ollama":
        from openai import OpenAI
        client = OpenAI(base_url=settings.ollama_base_url, api_key="ollama")  # api_key unused by ollama but required by sdk
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=max_tokens,
            temperature=0,
        )
        usage = resp.usage
        return ChatResult(
            text=resp.choices[0].message.content or "",
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
        )

    elif provider == "anthropic":
        import anthropic
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return ChatResult(
            text=resp.content[0].text,
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
        )

    elif provider == "openai":
        from openai import OpenAI
        client = OpenAI(api_key=settings.openai_api_key)
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_completion_tokens=max_tokens,
            temperature=0,
        )
        usage = resp.usage
        return ChatResult(
            text=resp.choices[0].message.content or "",
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
        )

    else:
        raise ValueError(f"Unknown provider: {provider!r}")
