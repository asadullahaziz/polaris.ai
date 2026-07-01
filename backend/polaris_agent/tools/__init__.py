"""LLM-callable tools that wrap the deterministic `matching` engine + DAL.

P0: empty registry. P1 fills the copilot subset (clarify_listing, get_comps,
estimate_market_value, compare_listings, check_mandate, read/write_memory,
draft_message, set_mandate, manage listings/buy-boxes); P2/P3 add rank_buyers,
record_outreach, assess_deal. Tools are gated per graph (architecture §7).
"""
