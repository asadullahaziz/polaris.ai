"""
Copilot tool subset (architecture §7, implementation_plan P1.4/P1.6/P1.8).

`copilot_tools(principal_id)` returns LangChain tools bound to one user, so the
tools never need config plumbing to know whose data they touch. Every factual
number comes from the deterministic engine (via the DAL); the LLM only decides
when to call and narrates the result.

`extract_listing_details` (P1.6) is the one tool that itself calls a model
(structured output) — parsing messy seller text into fields + the gaps a buyer
will ask about.
"""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from polaris_agent import dal
from polaris_agent.models import get_model


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
    ) -> dict:
        """Create a draft listing for the user from structured fields. Returns the new
        listing_id. `condition` is turnkey | cosmetic | full_gut."""
        return await dal.create_listing_from_fields(
            principal_id,
            {
                "address": address,
                "beds": beds,
                "baths": baths,
                "sqft": sqft,
                "lot_size_sqft": lot_size_sqft,
                "year_built": year_built,
                "condition": condition,
                "property_type": property_type,
                "asking_price": asking_price,
            },
        )

    @tool
    async def list_my_listings() -> list:
        """List the user's own listings (id, status, asking price, address, beds, sqft)."""
        return await dal.list_seller_listings(principal_id)

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
    async def set_mandate(
        listing_id: int,
        floor_price: float | None = None,
        instructions: str | None = None,
        autonomy: str | None = None,
        auto_reply: bool | None = None,
    ) -> dict:
        """Set or update the agent's mandate (playbook) for one of the user's listings.
        autonomy is assist | confirm_batch | auto_with_policy."""
        return await dal.set_mandate_for_listing(
            listing_id,
            principal_id,
            {
                "floor_price": floor_price,
                "instructions": instructions,
                "autonomy": autonomy,
                "auto_reply": auto_reply,
            },
        )

    @tool
    async def check_mandate(listing_id: int) -> dict:
        """Read the current mandate for one of the user's listings."""
        return await dal.get_mandate_for_listing(listing_id, principal_id)

    @tool
    async def read_memory(namespace: str = "general") -> list:
        """Recall durable facts remembered about this user (namespace-scoped)."""
        return await dal.read_memory(principal_id, namespace)

    @tool
    async def write_memory(content: str, namespace: str = "general") -> dict:
        """Remember a durable fact about this user so future chats stay consistent."""
        return await dal.write_memory(principal_id, content, namespace)

    @tool
    async def launch_outreach(
        listing_id: int, limit: int = 10, config: RunnableConfig = None
    ) -> dict:
        """Rank the buyers most likely to close on one of your listings and prepare an
        outreach batch for your approval. Persists a draft campaign (ranked shortlist +
        drafted openers) — NOTHING is sent until you approve it in the Outreach panel.
        Returns the ranked shortlist with a 'why this buyer' reason for each. Narrate the
        top few and tell the user to approve when ready; already-contacted buyers are
        marked skipped."""
        conv_id = None
        if config:
            conv_id = (config.get("configurable") or {}).get("conversation_id")
        return await dal.launch_outreach(
            principal_id, listing_id, conversation_id=conv_id, limit=limit
        )

    return [
        extract_listing_details,
        create_listing,
        list_my_listings,
        estimate_market_value,
        get_comps,
        set_mandate,
        check_mandate,
        read_memory,
        write_memory,
        launch_outreach,
    ]
