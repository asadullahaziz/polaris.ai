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

**Implementation underway — Phase 0 scaffolding is in.** The design phase is closed
(product definition, PRD, feature/flow spec, agent architecture, full data model/schema,
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

Backend suite green (`make test`): schema, engine, seed idempotency/rebase, copilot plumbing +
the P0 spike. **⛔ Gate to P2:** the copilot slice demoed in the browser (mandatory human
checkpoint — plan P1). **Resume:** demo P1, then P2 (outreach fan-out).

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
`AuthMiddlewareStack`), **media = MinIO** in compose via `django-storages` (S3 API,
swappable), schema authored as **Django models + migrations** (canonical, matched 1:1 to
the DDL), Inngest **kept**. ASGI server = **uvicorn**; SPA cross-origin handled by
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
docker-compose.yml       6 services, one .env; `docker compose up` = the whole stack
```

## Commands

- **Bring up the whole stack:** `docker compose up --build` (or `make up`). Frontend on
  http://localhost:3000, API on http://localhost:8000, Inngest dev UI on :8288, MinIO
  console on :9001. First boot generates migrations in-container, migrates, creates a demo
  login (`demo` / `demo12345`) + P0 geo fixtures, and runs `seed_kc` (P1 demo data).
- **The P1 copilot demo:** open http://localhost:3000/copilot and log in. Use a **seed seller**
  (`kc_seller_1` / `polaris123`) to see the 15 seeded listings and value them, or `demo` for a
  fresh intake. Seed buyers are `kc_buyer_1..15` (same password); they have buy-boxes + history.
- **Seed the demo data:** `make seed` (idempotent) · `make seed-reset` (rebuild w/ fresh dates).
- **Fresh-clone reset:** `make down-v` (drops volumes) then `make up`.
- **Run the test suite (the gate):** `make test` — `makemigrations && pytest` in the backend
  container (schema, matching engine, seed idempotency/rebase, copilot plumbing, P0 spike).
- **Migrations / shell / psql / format:** `make migrate` · `make shell` · `make psql` · `make fmt`.
- **Regenerate the typed FE client** (backend must be up): `cd frontend && npm run gen:api`.

> P0 note: DB migrations are generated in-container at boot (Django/GDAL can't run on the
> host). P1.1 commits them and reviews them 1:1 against the DDL.
