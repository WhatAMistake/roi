"""OpenAI-compatible client helpers."""

from typing import Any


THINKING_DISABLED = {"thinking": {"type": "disabled"}}


def apply_thinking_disabled(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Force DeepSeek V4 thinking off. Unknown to other models, so skip them."""
    model = str(kwargs.get("model") or "")
    if "deepseek" not in model.lower():
        return kwargs

    extra = dict(kwargs.get("extra_body") or {})
    extra["thinking"] = dict(THINKING_DISABLED["thinking"])
    kwargs["extra_body"] = extra
    return kwargs


def create_openai_client(**client_kwargs):
    """OpenAI client whose chat completions disable DeepSeek thinking."""
    from openai import OpenAI

    client = OpenAI(**client_kwargs)
    original_create = client.chat.completions.create

    def create(*args, **kwargs):
        return original_create(*args, **apply_thinking_disabled(kwargs))

    client.chat.completions.create = create
    return client
