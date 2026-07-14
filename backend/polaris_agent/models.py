"""
Provider-agnostic model wiring.

Model access hides behind a single `get_model(role)` with the provider chosen
by `settings.LLM_PROVIDER`. Graph/tool code never names a provider — swapping
is a settings change.

Roles:
  * workhorse  — copilot + away-responder
  * escalation — hard cases (unused today; kept as a wired step-up path)
  * bulk       — ranking/classification + auto-titling at volume

Defaults are the Anthropic lineup via OpenRouter, overridable per role via the
POLARIS_MODEL_* env vars. GPT models are usable through those vars;
`_accepts_temperature` keeps that path safe.
"""

from __future__ import annotations

import os
from functools import cache

from django.conf import settings

# Role -> (env var, documented default). Defaults are overridable per deploy.
_ROLE_DEFAULTS: dict[str, tuple[str, str]] = {
    "workhorse": ("POLARIS_MODEL_WORKHORSE", "anthropic/claude-sonnet-4.6"),
    "escalation": ("POLARIS_MODEL_ESCALATION", "anthropic/claude-opus-4.8"),
    "bulk": ("POLARIS_MODEL_BULK", "anthropic/claude-haiku-4.5"),
}


def _accepts_temperature(model_id: str) -> bool:
    """GPT-5-family reasoning models reject an explicit temperature (only the default
    is allowed — sending one is a 400). Everything else accepts it."""
    return not model_id.split("/", 1)[-1].startswith("gpt-5")


def model_id_for(role: str) -> str:
    try:
        env_var, default = _ROLE_DEFAULTS[role]
    except KeyError as exc:
        raise ValueError(f"unknown model role: {role!r}") from exc
    return os.environ.get(env_var, default)


@cache
def get_model(role: str = "workhorse", *, temperature: float = 0.0):
    """
    Return a LangChain chat model for the given role, built for the configured
    provider (`LLM_PROVIDER`). Provider client libs are imported lazily so this
    module stays importable even if a given provider isn't installed.
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
        kwargs: dict = {
            "model": model_id,
            "base_url": "https://openrouter.ai/api/v1",
            "api_key": settings.OPENROUTER_API_KEY,
        }
        if _accepts_temperature(model_id):
            kwargs["temperature"] = temperature
        return ChatOpenAI(**kwargs)

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
