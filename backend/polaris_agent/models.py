"""
Provider-agnostic model wiring (P0.14).

#5 (OpenRouter vs native Anthropic) is deferred, so model access hides behind a
single `get_model(role)` interface with the provider chosen by config. Nothing
in P0 actually calls a model — this is the seam so P1 (first real
structured-output use) can swap providers without touching graph code.

Roles (implementation_plan tech stack / CLAUDE.md):
  * workhorse  — copilot + auto-responder
  * escalation — hard cases
  * bulk       — ranking/classification at volume

NOTE (objective-partner flag): the project docs name the workhorse "Sonnet 4.6".
The concrete model IDs are intentionally env-driven (not hard-coded) and must be
confirmed against the current provider lineup when #5 is resolved — do not treat
the defaults below as authoritative.
"""

from __future__ import annotations

import os
from functools import lru_cache

from django.conf import settings

# Role -> (env var, documented default). Defaults are overridable per deploy.
_ROLE_DEFAULTS: dict[str, tuple[str, str]] = {
    "workhorse": ("POLARIS_MODEL_WORKHORSE", "anthropic/claude-sonnet-4.6"),
    "escalation": ("POLARIS_MODEL_ESCALATION", "anthropic/claude-opus-4.8"),
    "bulk": ("POLARIS_MODEL_BULK", "anthropic/claude-haiku-4.5"),
}


def model_id_for(role: str) -> str:
    try:
        env_var, default = _ROLE_DEFAULTS[role]
    except KeyError as exc:
        raise ValueError(f"unknown model role: {role!r}") from exc
    return os.environ.get(env_var, default)


@lru_cache(maxsize=None)
def get_model(role: str = "workhorse", *, temperature: float = 0.0):
    """
    Return a LangChain chat model for the given role, built for the configured
    provider (`LLM_PROVIDER`). Provider client libs are imported lazily so this
    module stays importable in P0 even if a given provider isn't installed.
    """
    provider = settings.LLM_PROVIDER
    model_id = model_id_for(role)

    if provider == "openrouter":
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "LLM_PROVIDER=openrouter requires langchain-openai to be installed."
            ) from exc
        return ChatOpenAI(
            model=model_id,
            temperature=temperature,
            base_url="https://openrouter.ai/api/v1",
            api_key=settings.OPENROUTER_API_KEY,
        )

    if provider == "anthropic":
        try:
            from langchain_anthropic import ChatAnthropic
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "LLM_PROVIDER=anthropic requires langchain-anthropic to be installed."
            ) from exc
        # Strip an "anthropic/" prefix if a provider-slug default is used.
        native_id = model_id.split("/", 1)[-1]
        return ChatAnthropic(
            model=native_id,
            temperature=temperature,
            api_key=settings.ANTHROPIC_API_KEY,
        )

    raise ValueError(f"unsupported LLM_PROVIDER: {provider!r}")
