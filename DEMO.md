# Polaris AI — demo recording script

The demo world is **Kessler County, WA** — 8 fictional towns built on real market data
(~3.2k properties, real prices/attributes/geometry). Every property has a street address
that resolves in the app; the world is closed by design (no geocoder). Addresses are
deterministic: the same ones come back on every rebuild, and `make seed` reprints the
cheat-sheet anytime.

Since 2026-07: the world ships **demo-ready personas** — every listing has a real
description, every buyer/seller carries standing `agent_instructions` (the live
behavior lever, inspectable in Settings → AI), mandates have must-haves and
availability windows, and a **hero path** is engineered so one flagship listing
produces four different agent outcomes, deterministically.

## Logins

| Who | Email | Password |
|---|---|---|
| **Walt Emerson** — hero seller (owns the flagship + 4 more) | `kc_seller_1@polaris.local` | `polaris123` |
| **Erin Kowalski** — buy-and-hold landlord (the *qualify → accept* buyer) | `kc_buyer_1@polaris.local` | `polaris123` |
| **Andre Bishop** — BRRRR operator (the *hold + diligence* buyer) | `kc_buyer_2@polaris.local` | `polaris123` |
| **Sofia Marchetti** — cash flipper (the *decline* buyer; closed a deal with Walt last week) | `kc_buyer_3@polaris.local` | `polaris123` |
| **Jake Tran** — first-timer whose ceiling sits under Walt's floor (the *impasse → escalation* buyer) | `kc_buyer_4@polaris.local` | `polaris123` |
| Fresh account (empty state) | `demo` | `demo12345` |

All `kc_buyer_1..15` / `kc_prospect_1..25` / `kc_seller_1..3` use `polaris123`.

## Copy-paste addresses

| | Address |
|---|---|
| **Flagship** (the hero listing — asking $464,000, floor $441,000) | `101 Rowan St, Norhaven, WA 98103` |
| Pre-warm closed deal (Walt × Sofia, a Norhaven fixer) | `104 Kestrel Dr, Norhaven, WA 98103` |
| Pre-warm stale outreach (never answered) | `118 Meridian Ln, Eastmere, WA 98115` |
| Unlisted (for the create-listing arc) | `100 Alder St, Norhaven, WA 98103` |
| Unlisted | `101 Maple St, Norhaven, WA 98103` |

Typing any fragment works — `rowan`, `kestrel`, or a town name like `norhaven`.
Dollar figures above come from seed-time calibration; if you reseed on a different
day they stay the same, but always trust the cheat-sheet `make seed` prints.

## Why the hero path works (30-second brief for the narrator)

The flagship's asking price is **calibrated against the live comp ARV** so its deal
margin lands at ~12.5% — dead center between the buy-and-hold threshold (10%), the
BRRRR threshold (15%), and the fix-and-flip decline line. Same listing, same numbers,
four mandates → four verdicts, computed by the deterministic engine, not the LLM:

- **Erin (buy_hold, 10%)** → clears her bar → her agent engages, negotiates, and any
  *accept* always arrives as a **draft she must sign**.
- **Andre (brrrr, 15%)** → borderline → his agent **holds** and asks pointed rehab/ARV
  questions, names no price.
- **Sofia (fix_flip, 20%)** → fails her bar → her agent **declines**, politely and firmly.
- **Jake (ceiling $427,770 < floor $441,000)** → both agents are gate-bounded: his can
  never offer enough, Walt's can never go low enough → the thread **stalls and
  escalates** to a human. The gate is structural, not prompt-deep.

## Pre-flight checklist (do all of these before recording)

1. **Reseed the same day**: `make seed-reset`. The calibration is checked in CI
   (`test_seed_kc_hero_divergence`), but a fresh rebase keeps the comp ages exact.
   Paste the printed cheat-sheet into a scratch buffer.
2. **Grace window**: `.env` already has `RESPONDER_GRACE_SECONDS=0` (agents reply
   immediately). For a more theatrical delay set it to `10` and restart the backend
   (`docker compose restart backend`).
3. **Browser profiles**: A = Walt, B = Erin, C = Jake (separate profiles or incognito
   windows so sessions don't collide).
4. **Presence is per-chat**: an away-agent stands down only for a thread whose reply box
   its principal has FOCUSED (clicked into) — merely having the thread open is fine, the
   agent still covers. So: keep buyers out of the reply box on their threads until their
   beat, and as Walt watch from **/deals** or the chat *list* — clicking into a thread's
   reply box is the human-takeover mechanic (scene 8), use it deliberately, not accidentally.
5. **Dry-run the divergence once off-camera**: run scene 4, watch the four replies
   land, then `make seed-reset` to restore a clean world before recording.

---

## The scenes

### Scene 1 — A lived-in pipeline (Walt)
**Route:** `/deals`, then `/chat`.
Log in as Walt. `/deals` already shows a pipeline: a **closed** deal with Sofia
Marchetti on Kestrel Dr (agreed price on the row) and a **contacted** deal that went
quiet six days ago. Open the Sofia chat from the deal row: a real transcript — the
"Polaris · Walt's assistant" badge on agent messages, the **propose** action chip on
the counter, Sofia's human replies in between. *Narrate: the agent pitched, negotiated,
and closed this while Walt watched.*

### Scene 2 — The flagship listing (Walt)
**Route:** `/listings` → open `101 Rowan St`.
Marketplace grid first (every card has a real description now), then the flagship
detail page. Read a line of the description — *composed from the county record, so
nothing here contradicts the engine*. Click **Run valuation** (as-is), toggle **ARV**;
show the comp table. Scroll to **Deal settings**: floor $441,000, must-haves
("proof of funds before contract"…), availability window, and Walt's mandate
instructions — *this is the contract his agent defends in every negotiation.*

### Scene 3 — Ranked buyers (Walt)
**Route:** `/buyers`.
Type `rowan` → pick the flagship → the county record card auto-fills → **Search**.
A ranked table with a real spread. Hover the top scores for the per-feature breakdown
(bought-in-area, price band, strategy, recency, cash). Call out **Sofia Marchetti**:
her reason includes the prior relationship — *the closed deal from scene 1 is feeding
the ranking engine*. Near the bottom: a lapsed investor or an out-of-towner with no
local history.

### Scene 4 — The copilot runs the same rails (Walt)
**Route:** `/polaris-ai`.
Type, in order:
1. `value 101 Rowan St, Norhaven` — comp-grounded range, same engine as scene 2.
2. `find buyers for my Rowan St listing` — same ranked spread as scene 3.
3. `reach out to Erin Kowalski, Andre Bishop, Sofia Marchetti and Jake Tran about my Rowan St listing`
   — the confirm card lists all four recipients with their opener text. **Approve.**
   The outreach rail shows the campaign; progress ticks land in the chat.
*Narrate: the agent is not a chatbot bolted on the side — every tool is the same API
the buttons call, and every write stops at a confirm card.*

### Scene 5 — Four mandates, four outcomes (Walt)
**Route:** `/chat` (stay on the LIST — don't open the threads yet).
Within seconds (grace = 0) the four threads move differently:
- **Erin** — engaged: her agent asks about mechanicals/tenants or names interest.
- **Andre** — **hold**: pointed diligence (rehab scope, what supports the ARV), no price.
- **Sofia** — **decline**: a firm, human pass ("margin isn't there for a flip").
- **Jake** — a lowball offer (his agent is capped by a ceiling he never reveals).
*Narrate: same listing, same engine numbers — the divergence is each buyer's private
mandate. And notice the voice: no bot-speak, the badge is the only tell.*

### Scene 6 — Negotiation → the accept draft (Walt, then Erin)
Let Walt's agent work Erin's thread unattended (do NOT open the thread as Walt —
watch the deal row move to **negotiating** on `/deals`). Walt's agent counters above
his floor; when Erin's agent judges the number clears her mandate it **accepts — and
the accept is never auto-sent**: it lands as a draft.
Switch to browser B (Erin): the bell shows **approval required** with the
recommendation; the chat shows the amber **DRAFT — only you see this** card.
Click **Approve & send**. Back as Walt: the deal flips to **agreed** on `/deals`.
*Fallback if no offer materialized: as Erin, type `can you do $455,000?` in the
thread, close the tab, and let the agents take it from there.*

### Scene 7 — The gate and the bell (Jake, then Walt)
Jake's thread stalls **by construction**: his agent can't clear $441,000, Walt's
can't go under it, and neither will regress its own offer. Show browser C (Jake):
the escalation lands on his bell — his agent hands the decision to the human instead
of breaking the mandate.
Then the seller-side version: from Jake's window ask
`are there any liens on the property? how old is the roof?` — Walt's agent posts
**nothing** to the counterparty (the answer isn't in the listing data) and an
escalation lands on **Walt's** bell. *Unanswerable ≠ improvised.*

### Scene 8 — Human takeover (Walt)
Open the escalated thread as Walt and **click into the reply box** (focusing it now
silences his agent for this chat); type a human answer ("roof was redone in 2019, no
liens, clean title"). *Narrate: clicking into the reply box IS the takeover — no mode
switch; the agent resumes cover when he clicks away or leaves.*

### Scene 9 — Governance: the persona is inspectable (Erin)
**Route:** `/settings` as Erin.
- **AI tab**: the away toggle, autonomy (auto-send vs draft-for-approval), the reply
  cap, and her **standing instructions** — the exact text that drove her agent's
  behavior in scenes 5-6. *Nothing was magic: this is the lever.*
- **Buy-boxes tab**: her Norhaven buy-box, strategy, price band, and mandate
  (ceiling never shown to counterparties, must-haves, availability).
- **Account tab**: bio and company — the personas are people, not rows.
Then `/polaris-ai` as Erin: `assess the Rowan St listing for my strategy` (her copilot
runs the same deal math from HER side), and the ad-hoc messaging path:
`message the seller of the Maple Hollow listing and ask about the roof` → confirm card
→ send (works outside any campaign).

### Scene 10 — The create-listing arc (Walt)
**Route:** `/buyers` → type `100 Alder St, Norhaven` (unlisted) → the county record
card fills → **Search** shows ranked buyers for a property with no listing →
**"Create a listing from this"** → the new-listing page arrives pre-matched
(attributes read-only) → **Attach → Create**.
Or the same via copilot: `create a listing for 100 Alder St, Norhaven at $300,000`
→ confirm card → `set a floor of $285,000 on it` → confirm card (set_mandate).

### Scene 11 — Wrap on the pipeline (Walt)
**Route:** `/deals`, then `/polaris-ai`.
Stage tabs: agreed (Erin), escalated-but-active (Jake), closed (Sofia), contacted
(the stale one). Ask the copilot: `where do my deals stand?` then
`mark the Kestrel Dr deal closed` (if not already) — confirm card, done.
*Close the loop: research → listing → outreach → negotiation → signature → CRM,
one agent, every write human-gated.*

---

## Knobs & troubleshooting

| Symptom / need | Fix |
|---|---|
| Agents reply too fast/slow on camera | `RESPONDER_GRACE_SECONDS` in `.env` (0 = instant, 45 = default), `docker compose restart backend` |
| An agent didn't reply | Its principal has that thread open (presence), their away-toggle is off, or the reply cap was hit (resets on their next human message) |
| Verdicts don't match the script | Stale world — `make seed-reset` (CI locks divergence via `test_seed_kc_hero_divergence`) |
| Need the addresses/logins again | `make seed` reprints the cheat-sheet (idempotent, no changes) |
| Reset a dirtied world between takes | `make seed-reset` — also clears all demo-session chats/deals for kc users |
| World density / towns / personas | `ROWS_PER_CLUSTER` / `TOWNS` / `ARCHETYPES` in `backend/catalog/management/commands/seed_kc.py`; prose lives in `_seed_content.py` |

Inngest dev UI on :8288 shows the outreach fan-out and `thread-inbound` runs live —
worth a picture-in-picture if the audience is technical.
