"""
The copilot tool suite (architecture §7; revisions §polaris-ai) — the *full agentic*
surface. `copilot_tools(principal_id)` returns LangChain tools bound to one user, so
tools never need config plumbing to know whose data they touch, and every write is
scoped to that user's own rows.

Two tiers (revisions "action safety posture — confirm every write"):
  * READS run freely — valuations, comps, rankings, lookups, listing/reading own data.
  * WRITES are propose → confirm → commit: the tool builds a structured proposal, calls
    `_confirm(...)` which raises a LangGraph **human-in-the-loop interrupt**, and only
    commits (via `dal`) once the user approves. The graph pauses at the interrupt; the
    consumer surfaces a confirm card and resumes with `Command(resume={"approved": …})`.
    This makes "nothing is written without an explicit tap" true BY CONSTRUCTION and
    generalizes v1's outreach-approval gate to every mutation.

Every factual number comes from the deterministic engine (via `dal`); the LLM decides
WHEN to call and narrates the WHY — it never invents a price, score, or id.
"""

from __future__ import annotations

import logging

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.types import interrupt
from pydantic import BaseModel, Field

from polaris_agent import dal
from polaris_agent.models import get_model

log = logging.getLogger(__name__)

# Names of the tools that go through the confirm-every-write interrupt (introspected
# by tests + the consumer's UX copy). write_memory is a low-stakes write and is exempt.
WRITE_TOOL_NAMES = {
    "create_listing",
    "update_listing",
    "set_mandate",
    "create_buy_box",
    "update_buy_box",
    "delete_buy_box",
    "launch_outreach_campaign",
    "send_messages",
    "update_deal_stage",
}


def _confirm(action: str, summary: str, proposal: dict) -> bool:
    """Human-in-the-loop gate for a write tool. Pauses the graph with a structured
    proposal (the client renders a confirm card) and resumes with the user's decision.

    Returns True only on an explicit approval. The code BEFORE this call re-runs when the
    graph resumes (the tool re-executes from the top), so it must stay side-effect-free —
    the commit lives strictly AFTER the gate.
    """
    decision = interrupt(
        {"kind": "confirm_write", "action": action, "summary": summary, "proposal": proposal}
    )
    if isinstance(decision, dict):
        return bool(decision.get("approved"))
    return bool(decision)


class OutreachTarget(BaseModel):
    """One buyer in a `launch_outreach_campaign` batch, with the listing(s) THEY matched."""

    user_id: int = Field(
        description="the buyer's user_id (from rank_buyers_for_listings / find_buyers)"
    )
    listing_ids: list[int] = Field(
        description="ONLY the listing(s) this buyer ranked for / should receive"
    )
    body: str | None = Field(
        None,
        description="personalized opener covering exactly those listings; "
        "omit for a plain default template",
    )


class OutgoingMessage(BaseModel):
    """One message in a `send_messages` batch — addressed to a PERSON, not a chat."""

    recipient_user_id: int = Field(
        description="the recipient's user_id — a chat counterparty from list_chats, a "
        "listing's seller_id, or a buyer from the rank/find tools"
    )
    body: str = Field(description="the message text, written personally for this recipient")
    listing_ids: list[int] = Field(
        default_factory=list,
        description="listing ids to attach for context (the user's own, or any active "
        "marketplace listing — e.g. the listing being asked about)",
    )


class ExtractedListing(BaseModel):
    """Structured listing fields parsed from free-form seller text."""

    address: str | None = Field(None, description="street address or area if given")
    property_type: str | None = Field(None, description="sfr | duplex | multifamily | condo | land")
    beds: int | None = None
    baths: float | None = None
    sqft: int | None = Field(None, description="living square footage")
    lot_size_sqft: int | None = None
    year_built: int | None = None
    condition: str | None = Field(None, description="turnkey | cosmetic | full_gut")
    asking_price: float | None = None
    notes: str | None = Field(None, description="anything else useful, verbatim if unsure")
    missing: list[str] = Field(
        default_factory=list,
        description="fields a buyer will ask about that were NOT provided (e.g. beds, condition, ARV)",
    )


def copilot_tools(principal_id: int) -> list:
    """Build the copilot tool set bound to `principal_id`."""

    # ============================ READS (run freely) ============================
    @tool
    async def extract_listing_details(raw_text: str) -> dict:
        """Parse messy seller text into structured listing fields and list the gaps a
        buyer will ask about. Use this first when the user pastes a property description."""
        model = get_model("workhorse").with_structured_output(ExtractedListing)
        parsed: ExtractedListing = await model.ainvoke(
            "Extract listing fields from this seller text. Only fill fields you are "
            "confident about; leave the rest null and add them to `missing`.\n\n"
            f"<seller_text>\n{raw_text}\n</seller_text>"
        )
        return parsed.model_dump()

    @tool
    async def lookup_property(address: str) -> dict:
        """Look up an existing property by address (fetch-existing dedup). Returns the
        existing Property read-only, or {found: false}. Never creates or edits."""
        return await dal.property_lookup(address)

    @tool
    async def search_properties(q: str, limit: int = 8) -> list:
        """Search known properties by partial address (closed-world autocomplete —
        there is no geocoder). Use when the user gives a fragment like a street or
        town name; returns matching properties with attributes and last sale."""
        return await dal.search_properties(q, limit)

    @tool
    async def list_my_listings() -> list:
        """List the user's own listings (id, title, status, asking price, address, beds)."""
        return await dal.list_seller_listings(principal_id)

    @tool
    async def browse_listings(q: str | None = None, limit: int = 20) -> list:
        """Browse the marketplace: ACTIVE listings from ALL sellers (id, title, asking
        price, address, seller name, owned_by_principal). Optional q filters by
        title/description/address fragment. Use when the user asks what's for sale,
        wants to evaluate someone else's listing, or hunts for deals."""
        return await dal.browse_listings(principal_id, q, limit)

    @tool
    async def get_listing(listing_id: int) -> dict:
        """Full detail for any listing visible to the user — their own (any status) or
        anyone's active one. The deal mandate is included ONLY for the user's own
        listings; other sellers' mandates are private and never present."""
        return await dal.get_listing_detail(listing_id, principal_id)

    @tool
    async def estimate_market_value(listing_id: int, after_repair: bool = False) -> dict:
        """Estimate a value range from comparable sales for any listing visible to the
        user (their own, or another seller's active one — market data, not private).
        Set after_repair=True for ARV (comped against good-condition sales). Returns
        low/point/high, the $/sqft basis, and the comps used."""
        res = await dal.estimate_for_listing(listing_id, principal_id, after_repair)
        if "comps" in res:
            res["comps"] = res["comps"][:6]  # trim for the narration
        return res

    @tool
    async def get_comps(listing_id: int) -> dict:
        """Return the nearest recent comparable SOLD properties for any listing visible
        to the user (their own, or another seller's active one), plus how far the
        search had to reach."""
        res = await dal.comps_for_listing(listing_id, principal_id)
        if "comps" in res:
            res["comps"] = res["comps"][:8]
        return res

    @tool
    async def check_mandate(listing_id: int) -> dict:
        """Read the current deal mandate (floor, must-haves, instructions) for one of the
        user's listings."""
        return await dal.get_mandate_for_listing(listing_id, principal_id)

    @tool
    async def list_my_buy_boxes() -> list:
        """List the user's own buy-boxes (criteria + geography count + deal settings)."""
        return await dal.list_buy_boxes(principal_id)

    @tool
    async def get_buy_box(buy_box_id: int) -> dict:
        """Read one of the user's buy-boxes in full."""
        return await dal.get_buy_box(principal_id, buy_box_id)

    @tool
    async def rank_buyers_for_listings(listing_ids: list[int], limit_per_listing: int = 10) -> dict:
        """Rank the buyers most likely to close on one or SEVERAL of the user's listings
        at once. Each buyer comes back with user_id, the listing(s) THEY matched, and a
        per-listing 'why this buyer' reason — exactly the shape launch_outreach_campaign
        needs, so a buyer matching two listings can get one opener covering both. Deterministic —
        narrate the reason, never the raw score. (This ranks + explains; sending outreach
        is a separate step.)"""
        return await dal.rank_buyers_for_listings(
            principal_id, listing_ids, limit_per_listing=limit_per_listing
        )

    @tool
    async def find_buyers(
        address: str,
        price: float | None = None,
        condition: int | None = None,
        beds: int | None = None,
        sqft: int | None = None,
        property_type: str | None = None,
        limit: int = 10,
    ) -> dict:
        """Find likely buyers ad-hoc from a property address + price (no listing saved).
        Resolves the address against known properties for geo; without a geo match the
        ranking degrades to price/strategy/history signals. Returns ranked buyers + reasons."""
        return await dal.find_buyers(
            principal_id,
            address,
            price=price,
            condition=condition,
            beds=beds,
            sqft=sqft,
            property_type=property_type,
            limit=limit,
        )

    @tool
    async def assess_deal(listing_id: int, strategy: str | None = None) -> dict:
        """Assess the wholesale spread on one of the user's listings → qualify / hold /
        decline, with the ARV/asking/rehab/spread math. `strategy` (fix_flip | brrrr |
        buy_hold | wholesale) sets the margin bar. Missing inputs → hold and ask."""
        return await dal.assess_deal_for_listing(listing_id, principal_id, strategy)

    @tool
    async def read_memory(namespace: str = "general") -> list:
        """Recall durable facts remembered about this user (namespace-scoped)."""
        return await dal.read_memory(principal_id, namespace)

    @tool
    async def list_chats(
        counterparty: str | None = None,
        involves_listing_id: int | None = None,
        awaiting_reply_only: bool = False,
        limit: int = 20,
    ) -> list:
        """List the user's 1:1 chats (most recent first) — use this to resolve people to
        chat_ids, e.g. before sending follow-ups. Filters (all optional, combinable):
        `counterparty` = name fragment; `involves_listing_id` = chats where that listing
        was shared (e.g. everyone contacted about it); `awaiting_reply_only` = the last
        message is ours and the other person hasn't answered yet. Each row has the
        counterparty, the last message, and whether we're awaiting their reply."""
        return await dal.list_chats(
            principal_id,
            counterparty=counterparty,
            involves_listing_id=involves_listing_id,
            awaiting_reply_only=awaiting_reply_only,
            limit=limit,
        )

    # =================== WRITES (propose → confirm → commit) ====================
    @tool
    async def create_listing(
        address: str,
        beds: int | None = None,
        baths: float | None = None,
        sqft: int | None = None,
        lot_size_sqft: int | None = None,
        year_built: int | None = None,
        condition: str | None = None,
        property_type: str | None = None,
        asking_price: float | None = None,
        title: str | None = None,
        description: str | None = None,
    ) -> dict:
        """Create a draft listing for the user from structured fields. Requires the user's
        confirmation before anything is saved. `condition` is turnkey | cosmetic | full_gut."""
        fields = {
            "address": address,
            "beds": beds,
            "baths": baths,
            "sqft": sqft,
            "lot_size_sqft": lot_size_sqft,
            "year_built": year_built,
            "condition": condition,
            "property_type": property_type,
            "asking_price": asking_price,
            "title": title,
            "description": description,
        }
        if not _confirm(
            "create_listing", f"Create a draft listing for {address}", {"fields": fields}
        ):
            return {"status": "cancelled", "action": "create_listing"}
        return await dal.create_listing(principal_id, fields)

    @tool
    async def update_listing(
        listing_id: int,
        title: str | None = None,
        description: str | None = None,
        asking_price: float | None = None,
        status: str | None = None,
        bundle_type: str | None = None,
    ) -> dict:
        """Update scalar fields on one of the user's listings (title, description, asking
        price, status, bundle type). Requires confirmation before saving."""
        fields = {
            k: v
            for k, v in {
                "title": title,
                "description": description,
                "asking_price": asking_price,
                "status": status,
                "bundle_type": bundle_type,
            }.items()
            if v is not None
        }
        if not _confirm(
            "update_listing",
            f"Update listing #{listing_id}",
            {"listing_id": listing_id, "fields": fields},
        ):
            return {"status": "cancelled", "action": "update_listing"}
        return await dal.update_listing(listing_id, principal_id, fields)

    @tool
    async def set_mandate(
        listing_id: int,
        floor_price: float | None = None,
        must_haves: list[str] | None = None,
        availability_window: str | None = None,
        instructions: str | None = None,
    ) -> dict:
        """Set or update the deal mandate for one of the user's listings (floor price,
        must-haves, availability, free-text instructions). Requires confirmation."""
        fields = {
            k: v
            for k, v in {
                "floor_price": floor_price,
                "must_haves": must_haves,
                "availability_window": availability_window,
                "instructions": instructions,
            }.items()
            if v is not None
        }
        if not _confirm(
            "set_mandate",
            f"Set the deal mandate for listing #{listing_id}",
            {"listing_id": listing_id, "fields": fields},
        ):
            return {"status": "cancelled", "action": "set_mandate"}
        return await dal.set_mandate_for_listing(listing_id, principal_id, fields)

    @tool
    async def create_buy_box(
        name: str,
        strategy: str,
        price_min: float | None = None,
        price_max: float | None = None,
        beds_min: int | None = None,
        sqft_min: int | None = None,
        property_types: list[str] | None = None,
        is_primary: bool | None = None,
        ceiling_price: float | None = None,
        must_haves: list[str] | None = None,
        instructions: str | None = None,
        geo: dict | None = None,
    ) -> dict:
        """Create a buy-box (acquisition criteria) for the user. `strategy` is fix_flip |
        buy_hold | brrrr | wholesale | new_construction. Optional deal settings (ceiling,
        must-haves, instructions) attach as its mandate; `geo` is one place/radius spec.
        Requires confirmation before saving."""
        fields = {
            k: v
            for k, v in {
                "name": name,
                "strategy": strategy,
                "price_min": price_min,
                "price_max": price_max,
                "beds_min": beds_min,
                "sqft_min": sqft_min,
                "property_types": property_types,
                "is_primary": is_primary,
                "ceiling_price": ceiling_price,
                "must_haves": must_haves,
                "instructions": instructions,
                "geo": geo,
            }.items()
            if v is not None
        }
        if not _confirm("create_buy_box", f"Create buy-box '{name}'", {"fields": fields}):
            return {"status": "cancelled", "action": "create_buy_box"}
        return await dal.create_buy_box(principal_id, fields)

    @tool
    async def update_buy_box(
        buy_box_id: int,
        name: str | None = None,
        strategy: str | None = None,
        price_min: float | None = None,
        price_max: float | None = None,
        beds_min: int | None = None,
        sqft_min: int | None = None,
        property_types: list[str] | None = None,
        is_primary: bool | None = None,
        is_active: bool | None = None,
        ceiling_price: float | None = None,
        must_haves: list[str] | None = None,
        instructions: str | None = None,
        geo: dict | None = None,
    ) -> dict:
        """Update one of the user's buy-boxes (criteria, deal settings, or add a geo).
        Requires confirmation before saving."""
        fields = {
            k: v
            for k, v in {
                "name": name,
                "strategy": strategy,
                "price_min": price_min,
                "price_max": price_max,
                "beds_min": beds_min,
                "sqft_min": sqft_min,
                "property_types": property_types,
                "is_primary": is_primary,
                "is_active": is_active,
                "ceiling_price": ceiling_price,
                "must_haves": must_haves,
                "instructions": instructions,
                "geo": geo,
            }.items()
            if v is not None
        }
        if not _confirm(
            "update_buy_box",
            f"Update buy-box #{buy_box_id}",
            {"buy_box_id": buy_box_id, "fields": fields},
        ):
            return {"status": "cancelled", "action": "update_buy_box"}
        return await dal.update_buy_box(principal_id, buy_box_id, fields)

    @tool
    async def delete_buy_box(buy_box_id: int) -> dict:
        """Delete one of the user's buy-boxes (and its geos + mandate). Requires confirmation."""
        if not _confirm(
            "delete_buy_box", f"Delete buy-box #{buy_box_id}", {"buy_box_id": buy_box_id}
        ):
            return {"status": "cancelled", "action": "delete_buy_box"}
        return await dal.delete_buy_box(principal_id, buy_box_id)

    @tool
    async def launch_outreach_campaign(
        recipients: list[OutreachTarget], config: RunnableConfig | None = None
    ) -> dict:
        """Launch an outreach CAMPAIGN pitching the user's listing(s) to selected buyers:
        each buyer gets ONE opener message (opening their 1:1 chat) attaching exactly the
        listing(s) THAT buyer matched — a buyer who matches two listings gets one message
        covering both. Pick buyers + their listing_ids from rank_buyers_for_listings (or
        find_buyers) first, and draft a personal body per buyer. The campaign machinery is
        the point: already-contacted (buyer, listing) pairs are skipped automatically (the
        delivery ledger), and after the user confirms, the sends run durably in the
        background with live progress. Use this whenever pitching the user's own listings
        to buyers — even a single one; for any other message (a follow-up, or contacting
        someone about THEIR listing), use send_messages."""
        recipients = [
            OutreachTarget.model_validate(r) if not isinstance(r, OutreachTarget) else r
            for r in (recipients or [])
        ]
        if not recipients:
            return {"status": "empty", "note": "no recipients to contact"}
        # Propose: validate + resolve names/addresses/ledger flags so the confirm card
        # shows real people, real listings, and the exact opener text. Read-only, so it
        # re-runs harmlessly when the graph resumes after the interrupt.
        specs = [r.model_dump() for r in recipients]
        preview = await dal.preview_outreach(principal_id, specs)
        if preview.get("error"):
            return preview
        n_listings = len({lid for r in recipients for lid in r.listing_ids})
        if not _confirm(
            "launch_outreach_campaign",
            f"Send outreach to {len(recipients)} buyer(s) across {n_listings} listing(s)?",
            {"recipients": preview["recipients"]},
        ):
            return {"status": "cancelled", "action": "launch_outreach_campaign"}

        # Commit: persist the campaign (via the pure ledger core), flip it to sending, and
        # fire the durable fan-out event. The Inngest emit lives HERE (not in dal), and is
        # best-effort — the campaign is already staged, so a dev-server hiccup only means
        # the fan-out is retried, never a lost campaign.
        ai_chat_id = (config or {}).get("configurable", {}).get("ai_chat_id")
        res = await dal.launch_outreach(principal_id, specs, ai_chat_id=ai_chat_id)
        campaign_id = res.get("campaign_id")
        if not campaign_id:
            return res  # validation error — nothing to dispatch
        approved = await dal.approve_campaign(principal_id, campaign_id)
        res["dispatched"] = False
        if approved.get("status") == "sending":
            try:
                import inngest

                from orchestration.client import inngest_client

                await inngest_client.send(
                    inngest.Event(name="outreach/approved", data={"campaign_id": campaign_id})
                )
                res["dispatched"] = True
            except Exception as exc:  # noqa: BLE001 - staged; fan-out will be retried
                log.warning("failed to emit outreach/approved: %s", exc)
                res["warning"] = "queued locally; fan-out event not delivered to Inngest"
        return res

    @tool
    async def send_messages(
        messages: list[OutgoingMessage], config: RunnableConfig | None = None
    ) -> dict:
        """Send message(s) to ANY user — a counterparty the user already talks to OR a
        brand-new contact: the 1:1 chat is opened automatically (there is exactly one
        chat per pair of users), so first contact and follow-up are the same move. E.g.
        ask a listing's seller about their property (address them by the listing's
        seller_id and attach that listing for context), or follow up with a buyer. Draft
        each body personally for its recipient; one confirmation card covers the whole
        batch; messages send as Polaris on the user's behalf. For pitching the user's
        OWN listings to buyers, use launch_outreach_campaign instead."""
        messages = [
            OutgoingMessage.model_validate(m) if not isinstance(m, OutgoingMessage) else m
            for m in (messages or [])
        ]
        if not messages:
            return {"status": "empty", "note": "no messages to send"}
        # Propose: validate recipients + attachments and resolve names so the confirm
        # card shows real people (and which sends open a NEW conversation). Read-only,
        # so it re-runs harmlessly when the graph resumes after the interrupt.
        preview = await dal.preview_message_sends(
            principal_id,
            [m.recipient_user_id for m in messages],
            [lid for m in messages for lid in m.listing_ids],
        )
        recipients = preview["recipients"]
        invalid = [m.recipient_user_id for m in messages if m.recipient_user_id not in recipients]
        if invalid:
            return {
                "status": "invalid_recipients",
                "invalid_user_ids": invalid,
                "note": "unknown user ids (or the user themself) — resolve recipients via "
                "list_chats, a listing's seller_id, or the buyer tools, then retry",
            }
        if preview["invalid_listing_ids"]:
            return {
                "status": "invalid_attachments",
                "invalid_listing_ids": preview["invalid_listing_ids"],
                "note": "these listings aren't visible to the user (not theirs and not "
                "active) — attach only listings you actually looked up",
            }
        if any(not m.body.strip() for m in messages):
            return {"status": "error", "note": "every message needs a non-empty body"}
        names = {uid: r["name"] for uid, r in recipients.items()}
        shown_names = [names[m.recipient_user_id] for m in messages]
        shown = ", ".join(shown_names[:3]) + (
            f" +{len(shown_names) - 3} more" if len(shown_names) > 3 else ""
        )
        proposal = {
            "messages": [
                {
                    "recipient_user_id": m.recipient_user_id,
                    "to": names[m.recipient_user_id],
                    "new_chat": recipients[m.recipient_user_id]["new_chat"],
                    "body": m.body,
                    "listing_ids": m.listing_ids,
                }
                for m in messages
            ]
        }
        if not _confirm(
            "send_messages",
            f"Send {len(messages)} message(s) to {shown}?",
            proposal,
        ):
            return {"status": "cancelled", "action": "send_messages"}

        # Commit: idempotent under this turn's thread_id (a resume replay can't double-send).
        thread_id = (config or {}).get("configurable", {}).get("thread_id") or "turn"
        res = await dal.send_messages(
            principal_id, [m.model_dump() for m in messages], f"copilot:{thread_id}"
        )
        # Live-deliver to any open chat window + arm each counterparty's away-responder
        # (the send is an inbound to their side) — both best-effort, like outreach.
        for r in res.get("results", []):
            payload = r.pop("message", None)
            if r.get("status") != "sent":
                continue
            r["to"] = names.get(r["recipient_user_id"])
            try:
                from channels.layers import get_channel_layer

                await get_channel_layer().group_send(
                    f"chat_{r['chat_id']}", {"type": "chat.message", "data": payload}
                )
            except Exception as exc:  # noqa: BLE001 - the message is persisted; WS is best-effort
                log.warning("follow-up broadcast failed for chat %s: %s", r["chat_id"], exc)
            try:
                import inngest

                from orchestration.client import inngest_client

                await inngest_client.send(
                    inngest.Event(
                        name="chat/inbound",
                        data={"chat_id": r["chat_id"], "inbound_message_id": r["message_id"]},
                    )
                )
            except Exception as exc:  # noqa: BLE001 - never fail the send on this
                log.warning("chat/inbound emit failed for chat %s: %s", r["chat_id"], exc)
        return {"status": "sent", **res}

    @tool
    async def list_deals(
        side: str | None = None, stage: str | None = None, listing_id: int | None = None
    ) -> list:
        """The user's deal pipeline (mini CRM): one deal per (listing, buyer) with a
        stage (contacted, engaged, negotiating, agreed, closed, lost), the standing
        disclosed offers, and the linked chat. Filter by side ('selling' | 'buying'),
        stage, or listing_id."""
        return await dal.list_deals(principal_id, side=side, stage=stage, listing_id=listing_id)

    @tool
    async def update_deal_stage(deal_id: int, stage: str) -> dict:
        """Manually move a deal to a stage (contacted, engaged, negotiating, agreed,
        closed, lost) — e.g. mark it closed after papers are signed, or lost if the
        buyer went dark. Requires the user's confirmation."""
        if not _confirm(
            "update_deal_stage",
            f"Move deal #{deal_id} to {stage!r}",
            {"deal_id": deal_id, "stage": stage},
        ):
            return {"status": "cancelled", "action": "update_deal_stage"}
        return await dal.update_deal_stage(principal_id, deal_id, stage)

    # write_memory is a low-stakes write — no confirmation gate (revisions exempt memory).
    @tool
    async def write_memory(content: str, namespace: str = "general") -> dict:
        """Remember a durable fact about this user so future chats stay consistent."""
        return await dal.write_memory(principal_id, content, namespace)

    return [
        # reads
        extract_listing_details,
        lookup_property,
        search_properties,
        list_my_listings,
        browse_listings,
        get_listing,
        estimate_market_value,
        get_comps,
        check_mandate,
        list_my_buy_boxes,
        get_buy_box,
        rank_buyers_for_listings,
        find_buyers,
        assess_deal,
        read_memory,
        list_chats,
        list_deals,
        # writes (confirm-gated)
        create_listing,
        update_listing,
        set_mandate,
        create_buy_box,
        update_buy_box,
        delete_buy_box,
        launch_outreach_campaign,
        send_messages,
        update_deal_stage,
        # low-stakes write (no gate)
        write_memory,
    ]
