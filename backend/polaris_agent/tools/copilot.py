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
    "launch_outreach",
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
    async def get_listing(listing_id: int) -> dict:
        """Full detail for one of the user's listings: every property + the deal mandate."""
        return await dal.get_listing_detail(listing_id, principal_id)

    @tool
    async def estimate_market_value(listing_id: int, after_repair: bool = False) -> dict:
        """Estimate a value range for one of the user's listings from comparable sales.
        Set after_repair=True for ARV (comped against good-condition sales). Returns
        low/point/high, the $/sqft basis, and the comps used."""
        res = await dal.estimate_for_listing(listing_id, principal_id, after_repair)
        if "comps" in res:
            res["comps"] = res["comps"][:6]  # trim for the narration
        return res

    @tool
    async def get_comps(listing_id: int) -> dict:
        """Return the nearest recent comparable SOLD properties for one of the user's
        listings, plus how far the search had to reach."""
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
    async def rank_buyers_for_listing(listing_id: int, limit: int = 10) -> dict:
        """Rank the buyers most likely to close on one of the user's listings, each with a
        plain-language 'why this buyer' reason. Deterministic — narrate the reason, never
        the raw score. (This ranks + explains; sending outreach is a separate step.)"""
        return await dal.rank_buyers(listing_id, principal_id, limit=limit)

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
    async def launch_outreach(
        listing_id: int, limit: int = 10, config: RunnableConfig | None = None
    ) -> dict:
        """Rank the buyers most likely to close on one of the user's listings, draft a
        personalized opener for each, and — on the user's confirmation — open the pair chat
        with each buyer and send it (the listing is attached). Deterministic ranking;
        templated openers. Requires confirmation before any message is sent."""
        # Propose: rank first so the confirm card shows the actual shortlist. Read-only, so
        # it re-runs harmlessly when the graph resumes after the interrupt.
        ranked = await dal.rank_buyers(listing_id, principal_id, limit=limit)
        if ranked.get("error"):
            return ranked
        rows = ranked.get("ranked", [])
        if not rows:
            return {"status": "no_buyers", "note": "No matching buyers found near this listing."}
        proposal = {
            "listing_id": listing_id,
            "buyers": [
                {"name": r["name"], "score": r["score"], "reason": r["reason"]} for r in rows
            ],
        }
        if not _confirm(
            "launch_outreach",
            f"Send personalized outreach to {len(rows)} buyer(s) for listing #{listing_id}?",
            proposal,
        ):
            return {"status": "cancelled", "action": "launch_outreach"}

        # Commit: persist the campaign (via the pure ledger core), flip it to sending, and
        # fire the durable fan-out event. The Inngest emit lives HERE (not in dal), and is
        # best-effort — the campaign is already staged, so a dev-server hiccup only means
        # the fan-out is retried, never a lost campaign.
        ai_chat_id = (config or {}).get("configurable", {}).get("ai_chat_id")
        res = await dal.launch_outreach(principal_id, listing_id, ai_chat_id=ai_chat_id, limit=limit)
        campaign_id = res.get("campaign_id")
        if not campaign_id:
            return res  # e.g. no buyers / listing error — nothing to dispatch
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
        get_listing,
        estimate_market_value,
        get_comps,
        check_mandate,
        list_my_buy_boxes,
        get_buy_box,
        rank_buyers_for_listing,
        find_buyers,
        assess_deal,
        read_memory,
        # writes (confirm-gated)
        create_listing,
        update_listing,
        set_mandate,
        create_buy_box,
        update_buy_box,
        delete_buy_box,
        launch_outreach,
        # low-stakes write (no gate)
        write_memory,
    ]
