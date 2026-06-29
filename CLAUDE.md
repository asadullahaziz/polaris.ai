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

Design phase, well underway — **no application code or scaffolding yet** (the repo is
documentation only). But the design is largely settled: product definition, PRD,
feature/flow spec, agent architecture, and a full data model/schema are drafted under
`.claude/`. The next step is the implementation plan.

Key design docs (keep these authoritative — update them when a decision changes):
- [`.claude/context/PRODUCT.md`](./.claude/context/PRODUCT.md) — product definition (what/why)
- [`.claude/docs/PRD.md`](./.claude/docs/PRD.md) — product requirements
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

**Not yet decided** (see `.claude/context/PRODUCT.md` §6/§8): auth, media storage,
search infra, hosting/CI, payments. **Do not silently pick one** of these — propose
options and confirm with the user before introducing a major dependency or service.

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

## Suggested structure (once code is added)

Not yet created — a likely layout is a separate Next.js frontend and a DRF backend
(e.g. `frontend/` and `backend/`, or separate repos). Confirm the preferred structure
with the user before scaffolding.

## Commands

No build, test, or run commands exist yet. Update this section once the project is
scaffolded (install, dev server, tests, lint, migrations, etc.).
