import json
from typing import Any

import litellm
from litellm import completion, acompletion

# Disable telemetry and reduce overhead
litellm.telemetry = False
litellm.drop_params = True

from app.config import get_settings

settings = get_settings()


def _resolve_model() -> str:
    """Map provider + model name to the LiteLLM model string."""
    match settings.llm_provider:
        case "ollama":
            return f"ollama/{settings.llm_model}"
        case "groq":
            return f"groq/{settings.llm_model}"
        case "openai":
            return settings.llm_model
        case "anthropic":
            return f"anthropic/{settings.llm_model}"
        case _:
            return f"ollama/{settings.llm_model}"


def _resolve_extra_kwargs() -> dict:
    if settings.llm_provider == "ollama":
        return {"api_base": settings.ollama_base_url}
    return {}


def chat(
    messages: list[dict[str, str]],
    temperature: float = 0.3,
    max_tokens: int = 2048,
    response_format: dict | None = None,
) -> str:
    """Synchronous chat completion. Returns the message text."""
    kwargs: dict[str, Any] = {
        "model": _resolve_model(),
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        **_resolve_extra_kwargs(),
    }

    if response_format:
        kwargs["response_format"] = response_format

    response = completion(**kwargs)
    return response.choices[0].message.content or ""


async def achat(
    messages: list[dict[str, str]],
    temperature: float = 0.3,
    max_tokens: int = 2048,
    response_format: dict | None = None,
) -> str:
    """Async chat completion. Returns the message text."""
    kwargs: dict[str, Any] = {
        "model": _resolve_model(),
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        **_resolve_extra_kwargs(),
    }

    if response_format:
        kwargs["response_format"] = response_format

    response = await acompletion(**kwargs)
    return response.choices[0].message.content or ""


def extract_json(
    messages: list[dict[str, str]],
    temperature: float = 0.1,
) -> dict | list:
    """Extract structured JSON. Falls back to parsing markdown code blocks if the model doesn't support native JSON mode."""
    try:
        raw = chat(
            messages=messages,
            temperature=temperature,
            response_format={"type": "json_object"},
        )
        return json.loads(raw)
    except Exception:
        raw = chat(messages=messages, temperature=temperature)
        return _parse_json_from_text(raw)


async def aextract_json(
    messages: list[dict[str, str]],
    temperature: float = 0.1,
) -> dict | list:
    """Async structured JSON extraction with fallback parsing."""
    try:
        raw = await achat(
            messages=messages,
            temperature=temperature,
            response_format={"type": "json_object"},
        )
        return json.loads(raw)
    except Exception:
        raw = await achat(messages=messages, temperature=temperature)
        return _parse_json_from_text(raw)


def _parse_json_from_text(text: str) -> dict | list:
    """Attempt to extract JSON from a text response (handles markdown code fences and preambles)."""
    import re
    text = text.strip()

    try:
        return json.loads(text)
    except Exception:
        pass

    # Try extracting inside markdown code blocks
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    if fence_match:
        try:
            return json.loads(fence_match.group(1).strip())
        except Exception:
            pass

    # Try finding the first '{' and matching '}' or '[' and ']'
    json_obj_match = re.search(r"(\{[\s\S]*\})", text)
    if json_obj_match:
        try:
            return json.loads(json_obj_match.group(1).strip())
        except Exception:
            pass

    json_arr_match = re.search(r"(\[[\s\S]*\])", text)
    if json_arr_match:
        try:
            return json.loads(json_arr_match.group(1).strip())
        except Exception:
            pass

    return {}
