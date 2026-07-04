# `_v1_reference/` — v1 port source (NOT loaded, NOT installed, NOT tested)

This directory holds the **v1 code the v2 rebuild ports FROM**. Nothing here is in
`INSTALLED_APPS`, imported at runtime, collected by pytest (`testpaths=["tests"]`),
or linted (`pyproject` excludes it). It exists so each phase can port/rewire its
slice against the v2 schema instead of digging through git history.

**Delete each piece as its phase consumes it** (the plan's "delete after redistribution").

| Reference | Ported to | Phase |
|---|---|---|
| `matching/engine.py`, `matching/management/seed_kc.py` | `matching/engine.py`, `catalog/management/` | P1 |
| `catalog/` (Property, Listing, ListingProperty, ListingMedia, serializers/views) | `catalog/` | P1 |
| `buyers/` (BuyBox, BuyBoxGeo, Purchase) | `catalog/` (BuyBox/BuyBoxGeo, `Sale`) | P1 |
| `agent_context/` (Mandate, AgentMemory, AgentActionLog) | `catalog/` (Mandate) + `ai/` (memory) | P1/P2 |
| `polaris_agent/` (dal, disclosure, models, state, prompts, graphs, tools) | `polaris_agent/` (rewired `dal`; airlock graphs) | P2/P4 |
| `conversations/` (models, responder_service, presence, consumers, functions, views) | `chat/` (+ `ai/`) | P3/P4 |
| `notifications/` (Notification, serializers/views) | `notifications/` | P3 |
| `outreach/` (service, functions, models, views) | `ai/` (outreach ledger + fan-out) | P5 |

See `.claude/plans/polaris_ai_v2_revisions_2026-07-03.md` (authoritative) and the base
`polaris_ai_v2_implementation_plan.md` port/rewire/delete map.
