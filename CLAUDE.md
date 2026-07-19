# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Working style

Be an objective partner, not a sycophant: push back when something is wrong; agree only when it's rooted in truth. Surface inconsistencies and resolve them before building on top of them.

## What this is

Polaris AI is an AI-powered real-estate portal (POC/MVP) connecting property buyers and sellers. The centerpiece is "Polaris", each user's AI real-estate agent: a copilot chat that does real work through tools (listing intake, valuation, buyer ranking, outreach), and an away assistant that covers the user's human-to-human chats while they're offline — under user-set governance, behind hard disclosure guardrails.

Design docs live in `.claude/` (gitignored, local-only): `context/PRODUCT.md` (product source of truth), `docs/TDD.md` (technical design), `docs/architecture.md` (the agent layer in depth), `docs/matching_and_data.md` (matching engine + demo seed), `context/domain_wholesaling.md` (domain primer), `docs/PRD.md` (original scope contract). `DEMO.md` at the repo root (tracked) is the demo script with seeded logins.

## Commands

The whole stack runs in Docker (postgres, redis, backend, frontend, inngest; one `.env`):

```bash
make up            # docker compose up --build; `make up-d` = detached
make down-v        # stop + drop volumes (fresh-clone reset)
make test          # backend suite in-container (makemigrations + pytest)
make fmt           # black + ruff --fix (backend, in-container)
make migrate       # also: makemigrations, shell, psql, logs, ps
make seed          # idempotent Kessler County demo seed; `make seed-reset` rebuilds it
make sync-prompts  # push code prompts to Langfuse (ARGS="--update --promote")
```

- Frontend http://localhost:3000 · API http://localhost:8000 · Inngest dev UI http://localhost:8288 · MinIO console http://localhost:9001
- Single test: `docker compose exec backend pytest tests/test_chat.py -q` (or `-k <expr>`)
- Regenerate the typed frontend API client after API changes (backend must be up): `cd frontend && npm run gen:api`
- Migrations must be generated in-container — the host lacks GDAL, so `manage.py` won't run on the host.
- First boot migrates, seeds the demo world, and prints a cheat-sheet of demo logins (see `DEMO.md`).

## Architecture

**Stack:** Next.js 15 App Router (Tailwind 4, ShadCN, TanStack Query, generated OpenAPI client) · Django 5.2 ASGI (DRF + Channels on uvicorn) · PostgreSQL + PostGIS · Redis (channel layer + presence) · Inngest (durable async: retries, fan-out, debounce) · LangGraph agents (Postgres checkpointer) · LLMs via OpenRouter — Sonnet 4.6 workhorse, Opus 4.8 escalation tier, Haiku 4.5 for screening/triage/titling (`POLARIS_MODEL_*` env overrides) · Langfuse for prompt management + tracing (optional: keyless runs on code fallbacks with zero network) · MinIO (S3-compatible object storage for listing photos — presigned direct-from-browser PUTs; prod is real S3/R2 by pure env switch, `STORAGE_*` vars).

**Backend — one Django app per domain:**

- `users` — custom email-login `User`; `UserProfile` holds the agent governance knobs (away-assistant enable, autonomy, instructions, reply cap)
- `catalog` — `Property`, `Listing` (bundle-native M2M via `ListingProperty`), `BuyBox`(+`BuyBoxGeo`), `Sale` (behavioral purchase history), `Mandate` (per-deal floor/ceiling + instructions); media is URL-only in the DB (`ListingMedia.url`; photo files live in MinIO/S3 via presigned PUT — `catalog/storage.py` is the dual-endpoint boto3 helper). `services.py` is the shared write seam — REST views and agent tools both go through it.
- `matching/engine.py` — deterministic PostGIS engine: `get_comps`, `estimate_value` (+ARV), `rank_buyers`, `assess_deal`. No LLM anywhere in scoring.
- `chat` — human-to-human messaging: one `Chat` per user pair, listings attach to messages; the away-responder's commit gate (`responder_service.py`), Redis presence, Inngest inbound debounce
- `ai` — copilot sessions (`AiChat`/`AiMessage`, the block-structured transcript that is the system of record — graphs rehydrate from the DB, not the LangGraph checkpoint), agent memory, and the outreach campaign/recipient ledger
- `deals` — mini CRM; pipeline stages are driven by agent and human actions in chat
- `notifications` — in-app only
- `polaris_agent` — import-isolated agent package: LangGraph graphs (`graphs/`), tools (`tools/`), prompts + the Langfuse registry (`prompts/`, `prompt_store.py`), disclosure gates (`disclosure.py`), tracing (`observability.py`), and `dal.py` — the only ORM path, every function user-scoped
- `orchestration` — Inngest client and function registration

**The agent layer:**

- Copilot: a full agentic assistant over WebSocket; tools mirror the API (broad filterable reads, narrow typed writes); every write is confirm-gated via LangGraph interrupt, persisted in `AiChat.pending_confirm` so a pending confirmation survives reloads.
- Away-responder: a two-stage airlock. Haiku injection screen → deterministic engine assessment → Stage 1 decides on private context (closed structured output — no slot for mandate figures) → code policy gate → Stage 2 drafts from public-only context → code literal-leak check → commit gate. Stage 2 cannot leak a limit it never saw.
- Commit gate (the "exactly one reply" invariant): `pg_advisory_xact_lock` + presence/reply-cap re-check + `dedup_key` with `ON CONFLICT DO NOTHING`. Presence (focus/typing) is human takeover; a second inbound while away escalates; escalation pauses the agent on that chat rather than killing it.
- Negotiation is gate-bounded: the agent may auto-propose only within the mandate, and only monotonically; accepting an offer is always a human-signed draft. Mandate figures reach the counterparty only via explicit share flags, with engine-rendered numbers.
- Outreach: the copilot ranks and drafts, the human approves, Inngest fans out one durable step per recipient against a partial-unique "sent" ledger — a listing reaches a buyer at most once, ever.

**Frontend:** `app/(app)/` pages — `polaris-ai` (copilot), `chat` (inbox + threads), `deals`, `listings` (incl. the marketplace), `buyers`, `settings`. Session-cookie auth + CSRF handled in `lib/api.ts`; WebSockets carry copilot streaming, chat, and presence.

## Hard rules

- The deterministic guardrails — `polaris_agent/disclosure.py`, the responder policy gate, the literal-leak output check, the commit gate — are code, never Langfuse prompts. A prompt edit must not be able to weaken the airlock.
- Prompt constants in `polaris_agent/prompts/__init__.py` are byte-parity-tested against the Langfuse registry (`tests/test_prompt_store.py`). Changing a prompt in code means updating the constant, then `make sync-prompts ARGS="--update"`.
- Tool docstrings in `polaris_agent/tools/` and structured-output model docstrings/Field descriptions are LLM-facing — editing them changes agent behavior.
- Model wiring stays provider-agnostic behind `polaris_agent/models.py`; `get_model` omits temperature for GPT-family model IDs (they reject it).
- The test suite is LLM-free by design; live-LLM smokes are skipped by default. Keep it that way — graph routing, gates, and services are tested deterministically.
- Still undecided (don't silently pick one; propose and confirm first): search infrastructure, hosting/CI, payments.
