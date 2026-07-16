"""
Prompt-store tests (LLM-free, network-free — the suite runs keyless).

The keystone is parity: with Langfuse disabled, ``prompt_store`` must produce
byte-identical output to the fallback composition functions in
``polaris_agent.prompts`` for every surface. That guarantee is what makes the
Langfuse integration a pure overlay — outage, missing keys, or tests all
degrade to the code fallbacks with zero behavior change.
"""

from __future__ import annotations

import os
import re

import pytest

from polaris_agent import observability, prompt_store, prompts


@pytest.fixture(autouse=True)
def _langfuse_disabled(settings, monkeypatch):
    """Force disabled mode and make any client construction an error — the
    hermetic path must never touch the SDK, let alone the network."""
    settings.LANGFUSE_ENABLED = False
    settings.LANGFUSE_PUBLIC_KEY = None
    settings.LANGFUSE_SECRET_KEY = None

    def _boom(*a, **k):  # pragma: no cover - only fires on regression
        raise AssertionError("Langfuse client constructed in disabled mode")

    monkeypatch.setattr(prompt_store, "langfuse_client", _boom)
    yield


# ---------------------------------------------------------------------------
# Parity: disabled-mode compile == legacy composers, byte for byte
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "display_name,agent_instructions",
    [
        (None, None),
        ("Walt Harlan", None),
        (None, "  Always mention flood zones.  "),
        ("Walt Harlan", "Always mention flood zones."),
    ],
)
async def test_parity_copilot_system(display_name, agent_instructions):
    legacy = prompts.copilot_system_prompt(
        display_name=display_name, agent_instructions=agent_instructions
    )
    cp = await prompt_store.compose_copilot_system(
        display_name=display_name, agent_instructions=agent_instructions
    )
    assert cp.text == legacy
    assert cp.is_fallback is True
    assert cp.version is None


@pytest.mark.parametrize("stance", ["buy_side", "sell_side", "neutral"])
@pytest.mark.parametrize("deal_stage", [None, "negotiating"])
async def test_parity_responder_decide(stance, deal_stage):
    legacy = prompts.responder_decide_prompt(stance, deal_stage)
    cp = await prompt_store.compose_responder_decide(stance, deal_stage)
    assert cp.text == legacy


@pytest.mark.parametrize("stance", ["buy_side", "sell_side", "neutral"])
@pytest.mark.parametrize("principal_name", [None, "Walt Harlan"])
async def test_parity_responder_draft(stance, principal_name):
    legacy = prompts.responder_draft_prompt(stance, principal_name)
    cp = await prompt_store.compose_responder_draft(stance, principal_name)
    assert cp.text == legacy


async def test_parity_responder_triage_and_screen():
    assert (await prompt_store.compose_responder_triage()).text == prompts.responder_triage_prompt()
    assert (await prompt_store.compose_responder_screen()).text == prompts.screen_prompt()


def test_parity_outreach_summary():
    """Renders exactly the inline outreach-summary prompt ai.functions falls back to."""
    got = prompt_store.compile(
        "outreach/summary", label="12 Birch Ln", sent="4", skipped="1", failed="0"
    ).text
    expected = (
        "You are Polaris, a real-estate copilot. In 1-2 warm, concrete sentences, "
        "summarize this outreach result for the seller. Use the exact numbers; invent "
        "nothing.\n\n"
        "Listing(s): 12 Birch Ln\n"
        "Buyers reached (chats opened): 4\n"
        "Skipped (already in contact): 1\n"
        "Failed: 0\n"
    )
    assert got == expected


def test_parity_auto_title():
    first = "I want to sell my duplex on Marrow Street"
    got = prompt_store.compile("copilot/auto-title", first_message=first).text
    expected = (
        "Write a 3-6 word title (no quotes, no trailing punctuation) for a "
        f"real-estate chat that begins with:\n{first}"
    )
    assert got == expected


# ---------------------------------------------------------------------------
# Registry integrity
# ---------------------------------------------------------------------------
_TOKEN_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def test_registry_completeness():
    for name, surface in prompt_store.SURFACES.items():
        body = prompt_store.fallback_body(name)
        assert body.strip(), f"{name}: empty fallback body"
        declared = set(surface.variables) | set(surface.suffix_vars)
        found = set(_TOKEN_RE.findall(body))
        assert found == declared, f"{name}: template vars {found} != declared {declared}"
        for part in surface.parts:
            if part.startswith("@"):
                assert part[1:] in prompt_store.FRAGMENTS, f"{name}: unknown fragment {part}"


def test_fragments_nonempty_and_referenced():
    referenced = {
        part[1:]
        for surface in prompt_store.SURFACES.values()
        for part in surface.parts
        if part.startswith("@")
    }
    for name, text in prompt_store.FRAGMENTS.items():
        assert text.strip(), f"fragment {name} is empty"
        assert name in referenced, f"fragment {name} is orphaned (no surface references it)"


def test_brace_safety():
    """No stray single braces anywhere — mustache is the only brace syntax
    allowed in prompt bodies, so a careless edit can't corrupt compilation."""
    for name in prompt_store.SURFACES:
        stripped = _TOKEN_RE.sub("", prompt_store.fallback_body(name))
        assert "{" not in stripped and "}" not in stripped, f"{name}: stray brace"
    for name, text in prompt_store.FRAGMENTS.items():
        assert "{" not in text and "}" not in text, f"fragment {name}: stray brace"


def test_variable_values_with_braces_pass_through():
    tricky = 'their note said "{{label}} costs {160k}" verbatim'
    got = prompt_store.compile(
        "outreach/summary", label=tricky, sent="1", skipped="0", failed="0"
    ).text
    assert tricky in got  # value not re-templated, braces untouched


def test_missing_or_extra_variables_raise():
    with pytest.raises(ValueError):
        prompt_store.compile("outreach/summary", label="x")  # missing vars
    with pytest.raises(ValueError):
        prompt_store.compile("responder/screen", bogus="y")  # unexpected var


# ---------------------------------------------------------------------------
# Langfuse body rendering (what sync_prompts pushes)
# ---------------------------------------------------------------------------
def test_langfuse_body_renders_composability_tags():
    body = prompt_store.langfuse_body("responder/decide")
    assert body.startswith("@@@langfusePrompt:name=core/domain|label=production@@@")
    assert "@@@langfusePrompt:name=responder/decide-instructions|label=production@@@" in body
    assert "{{stance_playbook}}" in body
    assert body.endswith("{{deal_stage_note}}")
    # composed bodies must reference fragments, never inline their text
    assert prompts.DOMAIN not in body


def test_langfuse_bodies_ordered_fragments_first():
    names = list(prompt_store.all_langfuse_bodies())
    first_surface = names.index(next(iter(prompt_store.SURFACES)))
    assert all(names.index(f) < first_surface for f in prompt_store.FRAGMENTS)


def test_fallback_body_never_contains_tags():
    for name in prompt_store.SURFACES:
        assert "@@@langfusePrompt" not in prompt_store.fallback_body(name)


# ---------------------------------------------------------------------------
# Disabled-mode short-circuits (prompt store + observability)
# ---------------------------------------------------------------------------
async def test_disabled_mode_never_touches_sdk():
    # The autouse fixture makes any client construction raise — so surviving
    # these calls proves the hermetic path.
    prompt_store.warm_up()
    assert prompt_store.current_versions(["copilot/system"]) == {"copilot/system": None}
    cp = await prompt_store.acompile("responder/triage")
    assert cp.is_fallback is True and cp.version is None


def test_observability_noop_when_disabled():
    cfg = {"configurable": {"thread_id": "t1"}}
    out = observability.callback_config(cfg)
    assert out == cfg
    assert "callbacks" not in out
    assert out is not cfg  # copy, never the same dict (confirm-cfg stays JSON-safe)
    with observability.trace_turn("copilot-turn", user_id="1", session_id="s") as t:
        t.record(outcome="ok")  # must be a silent no-op
    assert observability.tracing_enabled() is False
    observability.shutdown()  # no-op, no raise


# ---------------------------------------------------------------------------
# Optional live smoke (needs Langfuse keys; same gate style as the LLM smoke)
# ---------------------------------------------------------------------------
@pytest.mark.skipif(
    not (
        os.environ.get("POLARIS_LIVE_LLM")
        and os.environ.get("LANGFUSE_PUBLIC_KEY")
        and os.environ.get("LANGFUSE_SECRET_KEY")
    ),
    reason="live Langfuse smoke; set POLARIS_LIVE_LLM=1 + LANGFUSE_* keys",
)
def test_live_prompt_fetch(settings):
    settings.LANGFUSE_ENABLED = True
    settings.LANGFUSE_PUBLIC_KEY = os.environ["LANGFUSE_PUBLIC_KEY"]
    settings.LANGFUSE_SECRET_KEY = os.environ["LANGFUSE_SECRET_KEY"]
    cp = prompt_store.compile("copilot/system", personalization="")
    assert cp.is_fallback is False, "copilot/system not found in Langfuse — run sync_prompts"
    assert cp.version is not None
    assert cp.text  # composability tags resolved server-side
    assert "@@@langfusePrompt" not in cp.text
    prompt_store.langfuse_client().flush()
