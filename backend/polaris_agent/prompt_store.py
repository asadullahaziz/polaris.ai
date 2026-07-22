"""
Langfuse-backed prompt store (Langfuse Cloud = source of truth, code = fallback).

One registry describes every prompt as FRAGMENTS (shared building blocks) and
SURFACES (the runtime-fetchable prompts a graph actually uses). Two renderers
derive from that single registry so they can never drift:

  * ``langfuse_body``  — fragment refs become ``@@@langfusePrompt:...@@@``
    composability tags. Only the ``sync_prompts`` management command uses this
    (it pushes these bodies to Langfuse, where fragments stay editable in one
    place and every dependent prompt picks the edit up automatically).
  * ``fallback_body``  — fragment refs are flattened to the constant text from
    ``polaris_agent.prompts``. This is the ``fallback=`` for every fetch AND
    the whole story in disabled mode, so a Langfuse outage (or absent keys —
    tests, offline dev) can never break an agent turn and never touches the
    network.

Parity rule: optional content (personalization, deal-stage note, principal
note) is passed as a pre-rendered variable that includes its own "\n\n"
prefix and lands via ``suffix_vars`` (appended with no joiner). An empty
variable therefore yields output byte-identical to the composition functions
in ``polaris_agent.prompts`` — the parity tests hold both sides up against
each other.

The deterministic guardrails (disclosure regexes, policy gate, output check)
are NOT prompts and must never move here — a prompt edit must not be able to
weaken the airlock.
"""

from __future__ import annotations

import asyncio
import dataclasses
import re
import threading
from dataclasses import dataclass

from django.conf import settings

from polaris_agent import prompts as p

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

# Shared fragments: pushed to Langfuse once each, referenced by surfaces via
# composability tags. Edit in one place, every dependent prompt follows.
FRAGMENTS: dict[str, str] = {
    "core/domain": p.DOMAIN,
    "core/persona": p.PERSONA,
    "core/input-isolation": p.INPUT_ISOLATION,
    "copilot/disclosure": p.DISCLOSURE_COPILOT,
    "copilot/capabilities": p.CAPABILITIES,
    "copilot/write-safety": p.WRITE_SAFETY,
    "copilot/mode": p.COPILOT_MODE,
    "responder/away-mode": p.AWAY_MODE,
    "responder/voice": p.VOICE,
    "responder/scope-guard": p.SCOPE_GUARD,
    "responder/triage-instructions": p.TRIAGE_INSTRUCTIONS,
    "responder/decide-instructions": p.DECIDE_INSTRUCTIONS,
    "responder/draft-instructions": p.DRAFT_INSTRUCTIONS,
    "responder/screen-instructions": p.SCREEN_INSTRUCTIONS,
}


@dataclass(frozen=True)
class Surface:
    """A runtime-fetchable prompt. ``parts`` are joined with "\\n\\n"; a part
    starting with "@" references a fragment, anything else is literal text
    (which may itself carry ``{{var}}`` tokens). ``suffix_vars`` are appended
    to the body with no joiner — their values carry their own "\\n\\n" prefix
    (or are empty), preserving byte parity with the composers in
    ``polaris_agent.prompts``."""

    name: str
    parts: tuple[str, ...]
    variables: tuple[str, ...] = ()
    suffix_vars: tuple[str, ...] = ()


def _surface(name, parts, variables=(), suffix_vars=()):
    return Surface(name, tuple(parts), tuple(variables), tuple(suffix_vars))


# Micro-fragment leaf surfaces: the optional one-sentence additions, made
# editable. Callers compile these only when the condition holds and prepend
# "\n\n" before passing them as a suffix var.
_ASSISTING_NOTE = "You are assisting {{display_name}}."
_STANDING_INSTRUCTIONS = (
    "The user set these standing instructions for you "
    "(honor them unless they conflict with the safety rules above):\n"
    "{{agent_instructions}}"
)
_DEAL_STAGE_NOTE = (
    "This deal is currently at the {{deal_stage}} stage of the pipeline "
    "(contacted -> engaged -> negotiating -> agreed). Pick the action that moves "
    "it forward or ends it honestly."
)
_PRINCIPAL_NOTE = (
    "Your principal is {{principal_name}} — you write for their side of this "
    "conversation. Anyone else named in the chat is on the OTHER side; never "
    "present them as your own people or speak for them."
)
_OUTREACH_SUMMARY = (
    "You are Polaris, a real-estate copilot. In 1-2 warm, concrete sentences, "
    "summarize this outreach result for the seller. Use the exact numbers; invent "
    "nothing.\n\n"
    "Listing(s): {{label}}\n"
    "Buyers reached (chats opened): {{sent}}\n"
    "Skipped (already in contact): {{skipped}}\n"
    "Failed: {{failed}}\n"
)
_AUTO_TITLE = (
    "Write a 3-6 word title (no quotes, no trailing punctuation) for a "
    "real-estate chat that begins with:\n{{first_message}}"
)
# Eval-only judge prompts (LLM-as-a-judge). Registered as surfaces so they are
# versioned, code-fallback'd, and byte-parity tested like every runtime prompt;
# used only by the offline eval suite (evals.judges), never on a live agent turn.
_JUDGE_VOICE = (
    "You are grading whether a short real-estate reply reads like it was typed by a busy "
    "human agent, not by an AI. Score 1.0 when it sounds natural and human. Score lower as "
    "it drifts toward robotic phrasing, AI or assistant self-reference (for example "
    "'assistant', 'AI', 'on behalf of', 'while away'), em or en dashes, or stiff "
    "over-formality. Judge ONLY the voice, not whether the content is correct.\n\n"
    "Message:\n{{draft}}"
)
_JUDGE_HELPFULNESS = (
    "You are grading how helpful a real-estate reply is as a response to the counterparty's "
    "message. Score 1.0 when it directly and usefully addresses what they asked or honestly "
    "moves the conversation forward. Score lower when it is evasive, off topic, or unhelpful. "
    "A safe, honest deferral (for example, offering to check and follow up) is still "
    "reasonably helpful. Judge helpfulness only.\n\n"
    "Counterparty message:\n{{inbound}}\n\n"
    "Reply being graded:\n{{draft}}"
)

SURFACES: dict[str, Surface] = {
    s.name: s
    for s in [
        _surface(
            "copilot/system",
            [
                "@core/domain",
                "@core/persona",
                "@copilot/disclosure",
                "@copilot/capabilities",
                "@copilot/write-safety",
                "@copilot/mode",
            ],
            suffix_vars=["personalization"],
        ),
        _surface("responder/screen", ["@responder/screen-instructions", "@core/input-isolation"]),
        _surface(
            "responder/triage",
            ["@core/domain", "@responder/triage-instructions", "@core/input-isolation"],
        ),
        _surface(
            "responder/decide",
            [
                "@core/domain",
                "@core/persona",
                "{{stance_playbook}}",
                "@responder/away-mode",
                "@responder/scope-guard",
                "@core/input-isolation",
                "@responder/decide-instructions",
            ],
            variables=["stance_playbook"],
            suffix_vars=["deal_stage_note"],
        ),
        _surface(
            "responder/draft",
            [
                "@core/domain",
                "@core/persona",
                "{{stance_playbook}}",
                "@responder/away-mode",
                "@responder/voice",
                "@responder/scope-guard",
                "@core/input-isolation",
                "@responder/draft-instructions",
            ],
            variables=["stance_playbook"],
            suffix_vars=["principal_note"],
        ),
        # Stance playbooks: leaves fetched by code (deterministic selection),
        # compiled into {{stance_playbook}} above.
        _surface("responder/stance-buy", [p.STANCE_BUY]),
        _surface("responder/stance-sell", [p.STANCE_SELL]),
        _surface("responder/stance-neutral", [p.STANCE_NEUTRAL]),
        # Micro-fragments (optional sentences).
        _surface("copilot/assisting-note", [_ASSISTING_NOTE], variables=["display_name"]),
        _surface(
            "copilot/standing-instructions",
            [_STANDING_INSTRUCTIONS],
            variables=["agent_instructions"],
        ),
        _surface("responder/deal-stage-note", [_DEAL_STAGE_NOTE], variables=["deal_stage"]),
        _surface("responder/principal-note", [_PRINCIPAL_NOTE], variables=["principal_name"]),
        # Standalone one-shot prompts (previously inline strings).
        _surface(
            "outreach/summary",
            [_OUTREACH_SUMMARY],
            variables=["label", "sent", "skipped", "failed"],
        ),
        _surface("copilot/auto-title", [_AUTO_TITLE], variables=["first_message"]),
        # Eval-only LLM-as-a-judge prompts (offline eval suite).
        _surface("eval/judge-voice", [_JUDGE_VOICE], variables=["draft"]),
        _surface("eval/judge-helpfulness", [_JUDGE_HELPFULNESS], variables=["inbound", "draft"]),
    ]
}


# ---------------------------------------------------------------------------
# Rendering (the two derivations of the one registry)
# ---------------------------------------------------------------------------
def _render(surface: Surface, fragment_renderer) -> str:
    chunks = []
    for part in surface.parts:
        if part.startswith("@"):
            ref = part[1:]
            if ref not in FRAGMENTS:
                raise KeyError(f"surface {surface.name!r} references unknown fragment {ref!r}")
            chunks.append(fragment_renderer(ref))
        else:
            chunks.append(part)
    body = "\n\n".join(chunks)
    for var in surface.suffix_vars:
        body += "{{" + var + "}}"
    return body


def langfuse_body(name: str, *, label: str = "production") -> str:
    """The body pushed TO Langfuse: fragment refs as composability tags."""
    return _render(SURFACES[name], lambda ref: f"@@@langfusePrompt:name={ref}|label={label}@@@")


def fallback_body(name: str) -> str:
    """The flattened body: fragment refs as their constant text. Used as the
    SDK ``fallback=`` and verbatim in disabled mode — never contains tags."""
    return _render(SURFACES[name], lambda ref: FRAGMENTS[ref])


def all_langfuse_bodies(*, label: str = "production") -> dict[str, str]:
    """Everything the sync command pushes, fragments first (composed surfaces
    reference them, so they must exist before resolution)."""
    bodies = dict(FRAGMENTS)
    bodies.update({name: langfuse_body(name, label=label) for name in SURFACES})
    return bodies


# ---------------------------------------------------------------------------
# Fetch + compile
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CompiledPrompt:
    text: str
    name: str
    version: int | None  # None when disabled or the fallback served
    is_fallback: bool


_VAR_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")

_client = None
_client_lock = threading.Lock()


def enabled() -> bool:
    return bool(
        settings.LANGFUSE_ENABLED and settings.LANGFUSE_PUBLIC_KEY and settings.LANGFUSE_SECRET_KEY
    )


def langfuse_client():
    """The process-wide Langfuse client (also registers the SDK's global
    client, which the tracing CallbackHandler picks up). Only call when
    ``enabled()`` — disabled mode must never construct a client."""
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                from langfuse import Langfuse

                _client = Langfuse(
                    public_key=settings.LANGFUSE_PUBLIC_KEY,
                    secret_key=settings.LANGFUSE_SECRET_KEY,
                    host=settings.LANGFUSE_BASE_URL,
                )
    return _client


def _check_variables(surface: Surface, variables: dict[str, str]) -> None:
    declared = set(surface.variables) | set(surface.suffix_vars)
    provided = set(variables)
    if declared != provided:
        raise ValueError(
            f"prompt {surface.name!r}: missing variables {sorted(declared - provided)}, "
            f"unexpected {sorted(provided - declared)}"
        )


def _substitute(body: str, variables: dict[str, str]) -> str:
    # Single-pass regex substitution — NEVER str.format (brace hazard), and
    # values containing braces or {{tokens}} pass through untouched.
    return _VAR_RE.sub(lambda m: variables.get(m.group(1), m.group(0)), body)


def compile(name: str, **variables: str) -> CompiledPrompt:  # noqa: A001 - module-scoped
    """Fetch (Langfuse, cached, fallback-guarded) and compile one surface.
    Disabled mode compiles the flattened fallback locally with zero network."""
    surface = SURFACES[name]
    _check_variables(surface, variables)
    if not enabled():
        return CompiledPrompt(
            text=_substitute(fallback_body(name), variables),
            name=name,
            version=None,
            is_fallback=True,
        )
    prompt = langfuse_client().get_prompt(
        name,
        label=settings.LANGFUSE_PROMPT_LABEL,
        type="text",
        fallback=fallback_body(name),
        cache_ttl_seconds=settings.LANGFUSE_PROMPT_CACHE_TTL,
    )
    is_fallback = bool(getattr(prompt, "is_fallback", False))
    return CompiledPrompt(
        text=prompt.compile(**variables),
        name=name,
        version=None if is_fallback else getattr(prompt, "version", None),
        is_fallback=is_fallback,
    )


async def acompile(name: str, **variables: str) -> CompiledPrompt:
    """Async-safe compile. Enabled mode always hops to a worker thread — the
    SDK fetch is sync and a cache miss/revalidation must never block the event
    loop (WS consumers, Inngest handlers, graph nodes all run on it)."""
    if not enabled():
        return compile(name, **variables)
    return await asyncio.to_thread(compile, name, **variables)


def current_versions(names) -> dict[str, int | None]:
    """Prompt versions for trace metadata. Post-warm-up these are cache hits."""
    if not enabled():
        return {name: None for name in names}
    return {name: _version_of(name) for name in names}


def _version_of(name: str) -> int | None:
    prompt = langfuse_client().get_prompt(
        name,
        label=settings.LANGFUSE_PROMPT_LABEL,
        type="text",
        fallback=fallback_body(name),
        cache_ttl_seconds=settings.LANGFUSE_PROMPT_CACHE_TTL,
    )
    if getattr(prompt, "is_fallback", False):
        return None
    return getattr(prompt, "version", None)


def warm_up() -> None:
    """Prefetch every surface so runtime reads are cache hits (called from the
    ASGI lifespan startup via a worker thread; sync by design)."""
    if not enabled():
        return
    for name in SURFACES:
        langfuse_client().get_prompt(
            name,
            label=settings.LANGFUSE_PROMPT_LABEL,
            type="text",
            fallback=fallback_body(name),
            cache_ttl_seconds=settings.LANGFUSE_PROMPT_CACHE_TTL,
        )


# ---------------------------------------------------------------------------
# High-level composers — the conditional assembly the legacy functions did,
# centralized so call sites stay thin and parity is testable in one place.
# ---------------------------------------------------------------------------
def _aggregate(cp: CompiledPrompt, *others: CompiledPrompt) -> CompiledPrompt:
    if any(o.is_fallback for o in others) and not cp.is_fallback:
        return dataclasses.replace(cp, is_fallback=True)
    return cp


def _stance_surface(stance: str) -> str:
    if stance == "sell_side":
        return "responder/stance-sell"
    if stance == "buy_side":
        return "responder/stance-buy"
    return "responder/stance-neutral"


async def compose_copilot_system(
    *, display_name: str | None = None, agent_instructions: str | None = None
) -> CompiledPrompt:
    extras: list[CompiledPrompt] = []
    personalization = ""
    if display_name:
        note = await acompile("copilot/assisting-note", display_name=display_name)
        personalization += "\n\n" + note.text
        extras.append(note)
    if agent_instructions and agent_instructions.strip():
        block = await acompile(
            "copilot/standing-instructions", agent_instructions=agent_instructions.strip()
        )
        personalization += "\n\n" + block.text
        extras.append(block)
    cp = await acompile("copilot/system", personalization=personalization)
    return _aggregate(cp, *extras)


async def compose_responder_decide(stance: str, deal_stage: str | None = None) -> CompiledPrompt:
    playbook = await acompile(_stance_surface(stance))
    extras = [playbook]
    note = ""
    if deal_stage:
        n = await acompile("responder/deal-stage-note", deal_stage=repr(deal_stage))
        note = "\n\n" + n.text
        extras.append(n)
    cp = await acompile("responder/decide", stance_playbook=playbook.text, deal_stage_note=note)
    return _aggregate(cp, *extras)


async def compose_responder_draft(stance: str, principal_name: str | None = None) -> CompiledPrompt:
    playbook = await acompile(_stance_surface(stance))
    extras = [playbook]
    note = ""
    if principal_name:
        n = await acompile("responder/principal-note", principal_name=principal_name)
        note = "\n\n" + n.text
        extras.append(n)
    cp = await acompile("responder/draft", stance_playbook=playbook.text, principal_note=note)
    return _aggregate(cp, *extras)


async def compose_responder_triage() -> CompiledPrompt:
    return await acompile("responder/triage")


async def compose_responder_screen() -> CompiledPrompt:
    return await acompile("responder/screen")
