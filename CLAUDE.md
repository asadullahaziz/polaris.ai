# CLAUDE.md

Guidance for Claude Code (and other AI assistants) when working in this repository.

## Critical Persona & Behavioral Guidelines
You are too agreeable by default. I want you objective. I want a partner. Not a sycophant.
Only agree with me if its rooted in truth.  

## What this project is

**Polaris AI** is an online, AI-powered property and real estate portal for buying and
selling property, connecting **buyers** and **sellers**. Its centerpiece is **Polaris
AI** — an AI real estate agent **and** copilot that both *does the work* for either side
(agent mode: research, listing, buyer matching, outreach, communication) and *assists in
real time* (copilot mode: answering questions, drafting messages/outreach, and
context-aware deal coaching inside buyer↔seller chat).

Read [`.claude/context/PRODUCT.md`](./.claude/context/PRODUCT.md) for the full product
definition before making product or architecture decisions. Key domain concepts: Seller,
Buyer, Listing/Property, Buyer Preferences, Buyer Discovery & Ranking (outreach engine),
In-App Communication, and Polaris AI (agent mode vs. copilot mode).

## Current status

> **⚠️ v2 REBUILD — PLAN APPROVED (2026-07-03); P0–P6 BUILT + merged to `main` (95 backend tests green); P6 awaiting user review/demo. Demo data = the Kessler County world (2026-07-05): subsampled fictional towns, universal resolvable addresses, archetype personas, closed-world address autocomplete — see [`DEMO.md`](./DEMO.md).** Per-phase v2 progress lives in the plan doc's as-built ledgers (P2/P3/P5/P6) and the `project-v2-rebuild` memory — trust those, not the v1 "Current status" body below. The status below describes the built **v1** POC (the port-from reference). The v2 direction is a full-web-app rebuild-in-place + port. **Authoritative plan: [`.claude/plans/polaris_ai_v2_revisions_2026-07-03.md`](.claude/plans/polaris_ai_v2_revisions_2026-07-03.md)** (approved revisions; supersede the base [`polaris_ai_v2_implementation_plan.md`](.claude/plans/polaris_ai_v2_implementation_plan.md) where they overlap). Locked v2 changes: registered users only (prospects removed); custom email-login user (+ password reset); fused `conversation` split into human `chat` + `ai` tables; **free-form 1:1 chat — ONE chat per user-pair, listings as message attachments, no `subject_listing`/`author_side`**; the auto-responder is a single **away-assistant chatbot** (user-level enable, role-agnostic airlock); the copilot is a **full agentic assistant** (tools mirror the API, confirm-every-write); governance knobs (`auto_reply_when_away`/`agent_autonomy`/`agent_instructions`) on `UserProfile`; ShadCN frontend. Treat the v1 details below as port-from reference, not the v2 target, until rewritten per-phase.

**Implementation underway — Phases 0–3 built (all three LangGraph graphs).** The design phase
is closed (product definition, PRD, feature/flow spec, agent architecture, full data model/schema,
and the system-design review remediation, all under `.claude/`). Execution follows the
phased plan in [`.claude/plans/implementation_plan.md`](./.claude/plans/implementation_plan.md).

**Phase 0** (scaffolding + one-command bring-up + the hard-seam spike) is built and green.

**Phase 1 (Copilot end-to-end · Graph 1 + the King County seed)** is built:
- **Schema** — all 18 domain models (`accounts`/`catalog`/`buyers`/`conversations`/`outreach`/
  `agent_context`/`notifications`), migrations matched 1:1 to the DDL (partial-unique ledgers,
  CHECK patterns, composite PKs, GiST + functional indexes). Verified in Postgres.
- **`seed_kc`** — idempotent, date-rebased seed: ~21.4k KC comps + ~40 synthetic personas
  (25 prospect / 15 registered w/ buy-boxes + mandates) + 15 active listings priced below market.
  Wired into bring-up (entrypoint) and `make seed`.
- **Engine** (`matching/engine.py`) — deterministic `get_comps` + `estimate_value` (+ARV) over
  PostGIS with staged fallback. Unit-tested, no LLM.
- **Copilot runtime** — `polaris_agent` tools (extract/create-listing/value/comps/mandate/memory),
  composed prompt fragments, a ReAct graph (`create_react_agent` + shared checkpointer), and the
  `CopilotConsumer` WS (rehydrate transcript → stream `copilot.token`/`copilot.done` → persist →
  Haiku auto-title). Provider parity through OpenRouter verified (invoke/structured-output/tools).
- **REST** — copilot conversations, listings + on-demand `/valuation`, and the shared context
  store (memory + mandate + preferences).
- **Frontend** — a copilot chat UI (sidebar, streamed markdown, comp-table rendering) + a right
  rail for listings/valuation and the shared context editor.

**Phase 2 (Outreach fan-out · Graph 3)** is built:
- **`rank_buyers`** (`matching/engine.py`) — deterministic, behavioral-first weighted score
  (bought-in-area .28 / price-band .18 / strategy .15 / recency .12 / volume .10 / cash .07 /
  relationship .10 + a registered-only buy-box-completeness bonus) with a per-feature breakdown
  and a human "why this buyer" reason. No LLM; degrades gracefully for prospects.
- **`outreach/service.py`** — the invariant-bearing, pure-sync core: `launch_outreach` (rank →
  ledger-dedup → templated per-buyer openers → persist `campaign='awaiting_approval'` +
  `recipients='pending'` + notification), `approve_campaign`/`cancel_campaign` (batch send-gate),
  and `send_recipient` (the **ledger guarantee** — skip-if-sent + partial-unique `status='sent'`;
  opener idempotency via `dedup_key` + ON CONFLICT DO NOTHING; opens the one shared thread).
- **Inngest fan-out** (`outreach/functions.py`) — `outreach/approved`-triggered async fn: one
  durable step per recipient (`concurrency`, `retries`), **templated** `outreach.progress` ticks
  pushed to the seller's copilot chat over the channel layer (no LLM), then **one** narrated
  summary (templated fallback) persisted + pushed as `outreach.summary`.
- **Copilot integration** — `launch_outreach` is a real copilot tool (conversation threaded via
  `RunnableConfig`); `CopilotConsumer` joins group `copilot_{user_id}` and forwards the tick/summary
  events. Rank/draft/present IS the copilot turn (architecture §6) — no separate outreach LLM graph.
- **REST** — `/api/outreach/campaigns/` list/detail + `approve`/`cancel` (approve emits the
  fan-out event via `send_sync`).
- **Frontend** — an Outreach right-rail tab (ranked shortlist w/ reasons, approve/cancel) + live
  `outreach.progress`/`outreach.summary` handling in the chat.

**Phase 3 (Auto-responder · Graph 2 — buyer role first, seller role configurable)** is built:
- **`assess_deal`** (`matching/engine.py`) — deterministic wholesale math over the same comp engine:
  `spread = ARV − asking − est_rehab − wholesale_fee`, margin vs a strategy threshold →
  `qualify/hold/decline` + rationale. Missing inputs → **hold and ask**, never a blind decline. No LLM.
- **Two-stage airlock** (`polaris_agent/graphs/responder.py`) — a `StateGraph`: screen (Haiku
  injection check) → assess (engine) → **Stage 1 decide** (PRIVATE ctx → CLOSED `AgentDecision`, no
  floor/ceiling slot) → deterministic **policy gate** → **Stage 2 draft** (PUBLIC-only ctx, mandate
  NOT in scope) → deterministic **output check** (literal-leak scan) → send gate. Stage 2 can't voice
  a limit it never held — the airlock is structural. Engine tools called deterministically (collapsed
  like Graph 3), `role=buyer_agent|seller_agent` swaps a prompt fragment + mandate orientation.
- **The invariant** (`conversations/responder_service.py`, pure-sync) — the "exactly one reply"
  guarantee: `commit_reply` takes `pg_advisory_xact_lock`, re-checks presence + the reply cap
  (own-side agent replies since the last **same-side** human `< N=1`), and inserts under
  `dedup_key` + `ON CONFLICT DO NOTHING`. Human takeover needs no special code (same-side human
  resets the cap). `persist_draft`/`approve_draft` = the `assist`/`confirm` send gate (draft + notify,
  approve = takeover); `escalate` = status + notification, **no** counterparty message.
- **Presence** (`conversations/presence.py`) — Redis `presence:{conv}:{user}` w/ TTL; fail-safe = absent.
- **Inngest** (`conversations/functions.py`) — `thread/inbound`-triggered `thread_inbound`:
  `step.wait_for_event("thread/focused", if_exp=…, timeout=grace)` debounce (45s, env-overridable),
  early presence + cap re-check (2nd inbound while absent → **escalate**), then one Graph 2 turn;
  broadcasts the reply to the thread group. Outreach fan-out emits `thread/inbound` for registered
  buyers (prospects have no agent). Durability = Inngest retries + `message` idempotency (not the checkpoint).
- **ThreadConsumer** (`ws/thread/<id>/`) — presence (focus/blur/typing → `thread/focused` + broadcast),
  `message.send` (persist + broadcast `message.new` + emit `thread/inbound` for the counterparty),
  agent-reply handback over the `thread_{id}` group.
- **REST** — `/api/threads/` (inbox list/detail/messages, thread-scoped `mandate` GET/PUT for the
  auto-reply/autonomy toggle, `approve-draft`), `/api/notifications/` (feed + read).
- **Frontend** — an Inbox + thread view: live socket, counterparty presence, agent-vs-human
  authorship badges + action chips, auto-reply/autonomy toggle, draft approval, a notifications bell.

Backend suite green (`make test`, 37 passed): P0 spike, schema, engine (comps + `rank_buyers` +
`assess_deal`), seed idempotency/rebase, copilot plumbing, outreach (ledger + fan-out idempotency +
slice), and **P3 — the commit-gate invariant (one reply; takeover stands down; 2nd inbound escalates;
cap resets only on same-side human), the disclosure gates (policy + literal-leak output check),
assess_deal divergence, draft/approve, and responder routing** — all LLM-free. The live two-stage
airlock was smoke-verified end-to-end against seeded data + OpenRouter: a `qualify` reply whose body
never leaks the ceiling, and a prompt-injection inbound that escalates without replying. **⛔ Gate to
seller role / stretch:** the P3 slice demoed in the browser (mandatory human checkpoint — plan P3):
seller launches outreach → offline buyers' agents auto-reply (qualify/hold/decline divergence) → seller
watches replies land; opening a thread + typing = takeover. **Resume:** demo P3 buyer role, sign off,
then flip `role="seller"`; the agent↔agent multi-round loop remains **stretch** (do not build).

Key design docs (keep these authoritative — update them when a decision changes):
- [`.claude/context/PRODUCT.md`](./.claude/context/PRODUCT.md) — product definition (what/why)
- [`.claude/docs/PRD.md`](./.claude/docs/PRD.md) — product requirements
- [`.claude/docs/TDD.md`](./.claude/docs/TDD.md) — technical design (architecture · data model · AI pipeline · stack reasoning); synthesizes the docs below for Andy/Arbaz
- [`.claude/docs/features.md`](./.claude/docs/features.md) — features & user flows
- [`.claude/docs/architecture.md`](./.claude/docs/architecture.md) — Polaris agent architecture (LangGraph)
- [`.claude/docs/matching_and_data.md`](./.claude/docs/matching_and_data.md) — matching/ranking engine, comping & King County seed data
- [`.claude/context/data_model_decisions.md`](./.claude/context/data_model_decisions.md) — data model & schema decisions
- [`.claude/context/domain_wholesaling.md`](./.claude/context/domain_wholesaling.md) — domain primer

## Tech stack

**Decided:**
- **Frontend:** Next.js
- **Backend:** Python · Django REST Framework (DRF)
- **Database:** PostgreSQL **+ PostGIS** (buy-box geography + behavioral "bought-in-area" matching)
- **AI/LLM:** via **OpenRouter** — Sonnet 4.6 (workhorse: copilot + auto-responder),
  Opus 4.8 (escalation), Haiku 4.5 (bulk ranking/classification)
- **Agent framework:** **LangGraph** (copilot · auto-responder turn · outreach fan-out);
  checkpointer = Postgres (`langgraph-checkpoint-postgres`)
- **Durable execution / async orchestration:** **Inngest** (events, retries, fan-out, long human waits)
- **Real-time transport:** **WebSockets** (chat + presence on one socket)
- **No vector store in v1** — ranking is behavioral-first; revisit only if semantic recall is needed
- **Notifications:** in-app only (no email/SMS)

See `.claude/docs/architecture.md` and `.claude/context/data_model_decisions.md` for rationale.

**Resolved during Phase 0** (see `implementation_plan.md` §3): **auth = session cookies**
(Django sessions + DRF `SessionAuthentication`; WS auth free via Channels'
`AuthMiddlewareStack`), **media = URL-only** (no object storage in the demo — MinIO was
removed 2026-07-08; `ListingMedia` stores URLs), schema authored as **Django models +
migrations** (canonical, matched 1:1 to the DDL), Inngest **kept**. ASGI server = **uvicorn**; SPA cross-origin handled by
**CORS + credentials** (SameSite=Lax works same-site on localhost).

**Still not decided** (see `.claude/context/PRODUCT.md` §6/§8): #5 provider (OpenRouter
vs native Anthropic — **deferred**; model wiring is provider-agnostic behind
`polaris_agent/models.py`), search infra, hosting/CI, payments. **Do not silently pick
one** — propose options and confirm before introducing a major dependency or service.

## How to work in this repo

- **Confirm scope before building.** This is a POC/MVP being defined. Prefer clarifying
  the intended feature set over assuming it.
- **Keep `.claude/context/PRODUCT.md` authoritative.** If a product detail changes or a new decision is
  made, update `.claude/context/PRODUCT.md` (and `README.md` if user-facing) so docs stay the source of
  truth.
- **Respect the decided stack** (Next.js / DRF / PostgreSQL) unless the user changes it.
- **Match existing conventions** once code exists. There are none yet, so when scaffolding,
  follow idiomatic, current best practices for each framework and keep frontend/backend
  cleanly separated.
- **Flag open questions** (autonomy of the AI agent, matching signals, trust & safety,
  ownership verification, target market) rather than guessing — these are listed in
  `.claude/context/PRODUCT.md` §8.

## Structure

```
backend/                 Django 5.2 ASGI project (one deployable)
  config/                settings (base/dev), asgi.py (ProtocolTypeRouter), urls, lifespan
  accounts/ catalog/ buyers/ matching/ conversations/ outreach/
  agent_context/ notifications/ orchestration/   ← one app per domain (plan §2)
  polaris_agent/         import-isolated agent pkg: checkpointer, graphs/, tools/, prompts/, models, dal
  seed/data/             king_county_sales.csv (P1 seed_kc source)
  tests/                 P0 spike tests (the gate)
frontend/                Next.js 15 App Router (Tailwind 4, TanStack Query, session+CSRF client)
docker-compose.yml       5 services, one .env; `docker compose up` = the whole stack
```

## Commands

- **Bring up the whole stack:** `docker compose up --build` (or `make up` / `make up-d`). Frontend
  on http://localhost:3000, API on http://localhost:8000, Inngest dev UI on :8288.
  First boot generates migrations in-container, migrates, creates a demo
  login (`demo` / `demo12345`) + P0 geo fixtures, and runs `seed_kc` — the **Kessler County
  demo world** (~3.2k properties across 8 fictional towns, every one with a resolvable street
  address; archetype-varied buyer personas). The seed prints a demo cheat-sheet (addresses +
  logins); **see [`DEMO.md`](./DEMO.md) for the full demo script**.
- **The copilot demo:** open http://localhost:3000 and log in. Use the **seed seller**
  (`kc_seller_1@polaris.local` / `polaris123`) to see the 15 seeded listings and value them, or
  `demo` for a fresh intake. Seed buyers are `kc_buyer_1..15@polaris.local` (same password);
  they have buy-boxes + history. The `/buyers` Find Buyers page uses closed-world address
  autocomplete (`/api/properties/search`) — type a street or town fragment and pick.
- **The auto-responder demo:** as `kc_seller_1@polaris.local`, ask the copilot to "reach out to the best buyers
  for listing #N" and approve. The offline buyers' agents auto-reply after the grace window — open
  http://localhost:3000/inbox to watch the qualify/hold/decline replies land per thread. Opening a
  thread + typing is the human takeover (presence silences that side's agent). Shorten the wait for a
  live demo with `RESPONDER_GRACE_SECONDS` (default 45). Inngest dev UI on :8288 shows `thread-inbound`.
- **Fresh-clone reset:** `make down-v` (drops volumes) then `make up`.
- **Run the test suite (the gate):** `make test` — `makemigrations && pytest` in the backend
  container (schema, matching engine incl. `assess_deal`, seed idempotency/rebase/address
  resolvability, property search, copilot plumbing, outreach ledger/fan-out, the commit-gate
  invariant + disclosure gates, P0 spike). 95 passing, 2 skipped (live-LLM smokes).
- **Migrations / shell / psql / format:** `make migrate` · `make shell` · `make psql` · `make fmt`.
- **Regenerate the typed FE client** (backend must be up): `cd frontend && npm run gen:api`.

> P0 note: DB migrations are generated in-container at boot (Django/GDAL can't run on the
> host). P1.1 commits them and reviews them 1:1 against the DDL.
