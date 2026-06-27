# CLAUDE.md

Guidance for Claude Code (and other AI assistants) when working in this repository.

## # Critical Persona & Behavioral Guidelines
You are too agreeable by default. I want you objective. I want a partner. Not a sycophant.

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

Brand-new project. As of this writing the repo contains only documentation
(`README.md`, `.claude/context/PRODUCT.md`, `CLAUDE.local.md`) — **no application code or
scaffolding yet**.

## Tech stack

**Decided:**
- **Frontend:** Next.js
- **Backend:** Python · Django REST Framework (DRF)
- **Database:** PostgreSQL

**Not yet decided** (see `.claude/context/PRODUCT.md` §6/§8): auth, AI/LLM provider & agent framework,
vector store, real-time messaging, media storage, geospatial (PostGIS?), task queue,
search infra, email/notifications, hosting/CI, payments. **Do not silently pick one** of
these — propose options and confirm with the user before introducing a major dependency
or service.

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
