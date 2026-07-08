# Polaris AI — demo cheat sheet

The demo world is **Kessler County, WA** — 8 fictional towns built on real market data
(~3.2k properties, real prices/attributes/geometry). Every property has a street address
that resolves in the app; there is no geocoder — the world is closed by design.
Addresses are deterministic: the same ones come back on every rebuild, and
`make seed` reprints this cheat sheet anytime.

## Logins

| Who | Email | Password |
|---|---|---|
| Seller (has the 15 listings) | `kc_seller_1@polaris.local` | `polaris123` |
| Buyer (buy-box + history) | `kc_buyer_1@polaris.local` | `polaris123` |
| Fresh account (empty state) | `demo` | `demo12345` |

Buyers `kc_buyer_1..15` and history-only users `kc_prospect_1..25` all use `polaris123`.

## Copy-paste addresses

| | Address |
|---|---|
| Listed (has an active listing) | `122 Hollis Ct, Norhaven, WA 98103` |
| Listed | `111 Ashfern Ct, Maple Hollow, WA 98038` |
| Listed | `124 Alder Dr, Eastmere, WA 98115` |
| Unlisted (for the ad-hoc arc) | `100 Alder St, Norhaven, WA 98103` |
| Unlisted | `101 Maple St, Norhaven, WA 98103` |

Typing any fragment works too — try `alder`, `hollis`, or a town name like `norhaven`.

## The 3-step demo script

**1. Find buyers (the dispo moment).** Log in as `kc_seller_1` → **Buyers** page →
type `alder` → pick a suggestion. The county record card auto-fills price/beds/sqft
from the property record. Hit **Search**: a ranked buyer table appears with a real
spread — hover a score for the per-feature breakdown (bought-in-area, price band,
strategy, recency, cash). The top buyer is the town's anchor cash flipper; near the
bottom you'll find a lapsed investor or an out-of-towner whose buy-box covers the
area but who has no local history. **Message** opens a chat with any of them.

**2. Create the listing.** From the results, click **"Create a listing from this"** —
the new-listing page arrives with the property already matched (attributes read-only,
no re-typing). **Attach → Create**. The address flows everywhere: listings, chat
attachments, outreach.

**3. The agent does the same (agent == API).** Open the copilot chat and try:
- *"search properties on Alder"*
- *"find buyers for 122 Hollis Ct, Norhaven"*
- *"reach out to the best buyers for my Hollis Ct listing"* → approve the outreach,
  then watch offline buyers' away-agents reply in **Chat** — pointed diligence
  questions and real offers in a human voice (the "Polaris" badge is the only tell),
  with the seller's agent answering from real comps and countering above the floor.
  Opening a thread and typing is the human takeover.

**4. Deals (mini CRM).** Every outreach/inquiry opens a deal on **/deals**: watch
stages advance (contacted → engaged → negotiating) as the agents talk. An in-bound
offer that clears your floor becomes an **accept draft** + a notification with the
recommendation — the human signs; approving it marks the deal **agreed**. Ask the
copilot *"where are my deals?"* or *"mark deal #N closed"*. Unanswerable questions
(liens, roof age) post nothing and escalate to your bell instead.

## Useful knobs

- `RESPONDER_GRACE_SECONDS` (default 45) — shorten the away-assistant wait for live demos.
- `ROWS_PER_CLUSTER` / `TOWNS` / `ARCHETYPES` in
  `backend/catalog/management/commands/seed_kc.py` — world density, town names,
  buyer-persona shapes.
- `make seed-reset` — rebuild the world with fresh dates (same addresses).
