"""
Human-friendly activity labels for copilot tools (2026-07-10).

One backend-side source of truth: the live WS event (`copilot.tool`) carries the label
while a tool runs, and `dal.save_turn_blocks` stamps it into the persisted tool row's
`tool_calls` JSON — so a reopened chat renders the same chips without the frontend ever
holding a copy of this map. Unknown tools humanize instead of leaking snake_case.
"""

from __future__ import annotations

TOOL_LABELS: dict[str, str] = {
    "extract_listing_details": "Reading the listing details…",
    "lookup_property": "Looking up the property…",
    "search_properties": "Searching properties…",
    "list_my_listings": "Getting your listings…",
    "browse_listings": "Browsing the marketplace…",
    "get_listing": "Opening the listing…",
    "estimate_market_value": "Valuing the property…",
    "get_comps": "Running comps…",
    "check_mandate": "Checking the mandate…",
    "list_my_buy_boxes": "Getting your buy boxes…",
    "get_buy_box": "Opening the buy box…",
    "rank_buyers_for_listings": "Ranking buyers…",
    "find_buyers": "Finding buyers…",
    "assess_deal": "Assessing the deal…",
    "read_memory": "Recalling notes…",
    "write_memory": "Saving a note…",
    "list_chats": "Checking your chats…",
    "list_deals": "Checking your deals…",
    "update_deal_stage": "Updating the deal…",
    "create_listing": "Creating the listing…",
    "update_listing": "Updating the listing…",
    "set_mandate": "Setting the mandate…",
    "create_buy_box": "Creating the buy box…",
    "update_buy_box": "Updating the buy box…",
    "delete_buy_box": "Deleting the buy box…",
    "launch_outreach_campaign": "Preparing outreach…",
    "send_messages": "Drafting messages…",
}


def tool_label(name: str | None) -> str:
    """The display label for a tool, falling back to humanized snake_case so a new
    tool never shows a raw identifier in the chat."""
    if not name:
        return "Working…"
    label = TOOL_LABELS.get(name)
    if label:
        return label
    return name.replace("_", " ").strip().capitalize() + "…"
