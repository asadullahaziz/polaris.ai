"""
_seed_content — the persona/prose layer of the Kessler County seed (demo realism).

Pure content: no ORM, no RNG, no Django imports. Every function is deterministic in
its inputs; variation is keyed off pk/index arithmetic so adding or editing content
never disturbs seed_kc's RNG stream.

Authoring contract (enforced by tests/test_seed.py):
  * Descriptions state only what the Property row supports (condition, sqft, beds,
    year_built, ...) — the engine owns all dollar figures (ARV, rehab, rent), so the
    prose never names one and can never contradict a deterministic diligence answer.
  * agent_instructions / mandate instructions must pass polaris_agent.disclosure
    .style_check verbatim (no em/en dashes, no bot-speak, <500 chars) and contain no
    dollar figures at all — limits are referred to abstractly ("my floor"), because
    the literal-leak scan only knows the mandate values.
"""

from __future__ import annotations

# The leading-underscore filename keeps Django's command discovery from registering
# this module as a management command.

# ---- listing descriptions ---------------------------------------------------------

_ERA_UNKNOWN = "home"


def _era(year_built) -> str:
    if not year_built:
        return _ERA_UNKNOWN
    if year_built < 1940:
        return "prewar build"
    if year_built < 1970:
        return "mid-century"
    decade = (year_built // 10 * 10) % 100
    if year_built >= 2000:
        return f"{year_built // 10 * 10}s build"
    return f"{decade}s build"


def _fmt_baths(baths) -> str | None:
    if baths is None:
        return None
    f = float(baths)
    return str(int(f)) if f == int(f) else f"{f:g}"


def _bb_era(prop) -> str:
    """'3 bed, 1.75 bath mid-century' — only claims fields the row actually has."""
    bits = []
    if prop.beds:
        bits.append(f"{prop.beds} bed")
    baths = _fmt_baths(prop.baths)
    if baths:
        bits.append(f"{baths} bath")
    core = ", ".join(bits) if bits else "single family"
    return f"{core} {_era(prop.year_built)}"


_GUT_OPENERS = [
    "Contractor special in {town}: a {bb} on a street of better kept homes.",
    "Project house in {town}, a {bb} that needs everything.",
    "Value add opportunity in {town}: {bb}, rough today, real upside done right.",
]
_GUT_BODY = (
    "The interior is original throughout and it shows, so plan on a full renovation, "
    "surfaces and systems both, with {sqft} sqft to work with. Bring your contractor "
    "to the walkthrough and write your scope from what you see."
)
_COSMETIC_OPENERS = [
    "Solid {bb} in {town}, {sqft} sqft on a quiet street.",
    "Honest {bb} in {town} with {sqft} sqft and good bones.",
    "Well kept but dated {bb} in {town}, {sqft} sqft.",
]
_COSMETIC_BODY = (
    "Kitchen and baths are dated but clean, roof and furnace serviceable. This is a "
    "straightforward cosmetic refresh, mostly paint, floors, and kitchen, not a gut."
)
_TURNKEY_OPENERS = [
    "Move in ready {bb} in {town}, {sqft} sqft and well cared for.",
    "Turnkey {bb} in {town}, {sqft} sqft with nothing major outstanding.",
    "Clean and current {bb} in {town}, {sqft} sqft.",
]
_TURNKEY_BODY = (
    "Systems are in good shape, the mechanicals have been maintained, and the "
    "interior shows well. Rent it as it sits or move in, there is no project here."
)

_TOWN_FLAVOR = {
    "Norhaven": (
        "Norhaven rents strong, with the college crowd close by and commuters "
        "after the quick corridor access."
    ),
    "Eastmere": "Eastmere is a steady family market, blocks of postwar homes that turn over slowly.",
    "Windmere": "Windmere sits close to the water, small lots, strong demand, quiet streets.",
    "Southglen": (
        "Southglen is a value market on the upswing, investors have been active "
        "here for two years running."
    ),
    "Kilbourne": (
        "Kilbourne is a working neighborhood with dependable rental demand near "
        "the transit lines."
    ),
    "Redfern": "Redfern draws tech commuters and rents near the top of the county.",
    "Kirkwell": "Kirkwell is quiet and established, mostly owner occupied with good schools.",
    "Bellamy": "Bellamy is the premium pocket of the county, larger homes and patient money.",
    "Maple Hollow": (
        "Maple Hollow trades at family friendly prices with acreage style lots on " "the town edge."
    ),
    "Carverton": (
        "Carverton is an affordable growth market where first time buyers and " "landlords compete."
    ),
    "Fernway": "Fernway has some of the most accessible price points in the county and rents well.",
    "Renwick": "Renwick mixes older housing stock with new infill, plenty of value add activity.",
}
_TOWN_FLAVOR_DEFAULT = "A steady Kessler County market with dependable demand."

_CLOSINGS = [
    "Sold as is, priced to reflect it. Walkthroughs by appointment.",
    "Seller prefers a {close_days} day close and can accommodate faster for a clean offer.",
    "First time on the market in years. Serious buyers can get access quickly.",
]


def compose_description(prop, town: str, *, close_days: int = 21, variant: int = 0) -> str:
    """Compose a listing description strictly from the property row's attributes.
    `variant` (pk-derived) rotates openers/closings so the 15 listings read distinct."""
    cond = prop.condition if prop.condition is not None else 3
    sqft = f"{prop.sqft:,}" if prop.sqft else "unrecorded"
    bb = _bb_era(prop)
    if cond <= 2:
        opener, body = _GUT_OPENERS[variant % 3], _GUT_BODY
    elif cond == 3:
        opener, body = _COSMETIC_OPENERS[variant % 3], _COSMETIC_BODY
    else:
        opener, body = _TURNKEY_OPENERS[variant % 3], _TURNKEY_BODY

    extras: list[str] = []
    if prop.waterfront:
        extras.append("Waterfront parcel, a genuine rarity here.")
    if (prop.view_rating or 0) >= 3:
        extras.append("Big views from the main level.")
    if prop.yr_renovated:
        extras.append(f"Partially updated in {prop.yr_renovated}.")
    if prop.sqft_basement:
        extras.append(
            f"There is {prop.sqft_basement:,} sqft of basement, useful as storage "
            "or future finished space."
        )
    if prop.floors is not None and float(prop.floors) >= 2:
        extras.append("Two levels with the bedrooms upstairs.")

    parts = [
        opener.format(town=town, bb=bb, sqft=sqft),
        body.format(sqft=sqft),
        *extras[:2],
        _TOWN_FLAVOR.get(town, _TOWN_FLAVOR_DEFAULT),
        _CLOSINGS[variant % 3].format(close_days=close_days),
    ]
    return " ".join(parts)


# ---- personas ---------------------------------------------------------------------


def _last_name(full_name: str) -> str:
    return full_name.split()[-1] if full_name else "Kessler"


SELLER_PERSONAS = [
    {  # kc_seller_1 — Walt Emerson, the hero seller
        "bio": (
            "Wholesaler and dispo operator moving a handful of contracts a month "
            "across Kessler County. Speed and certainty over top dollar."
        ),
        "company": "Emerson Property Group",
        "agent_instructions": (
            "I run a small dispo operation and speed matters more than squeezing the "
            "last dollar. Answer condition and price questions directly, lead with "
            "comps when challenged, and push every conversation toward a number or a "
            "walkthrough. Concede in small steps and never discuss my floor. If a "
            "buyer asks for documents, photos, inspection reports, liens, or anything "
            "you cannot verify from the listing, hand it to me instead of guessing."
        ),
    },
    {  # kc_seller_2 — Rosa Delgado
        "bio": (
            "Managing the family estate portfolio, selling a few long held houses " "the right way."
        ),
        "company": "Delgado Estates",
        "agent_instructions": (
            "These are family estate properties and I want them handled warmly but "
            "firmly. Favor buyers who can close without financing drama, answer "
            "condition questions honestly from the listing, and hold close to asking. "
            "My floor stays private and is not negotiable. Anything about documents, "
            "probate, or timelines comes to me first."
        ),
    },
    {  # kc_seller_3 — Curtis Vann
        "bio": "Retiring landlord selling down a rental portfolio built over twenty years.",
        "company": "Vann Rentals",
        "agent_instructions": (
            "I am divesting rentals and I do not chase buyers. Quote the numbers, "
            "answer what the listing supports, and let the deal speak for itself. One "
            "small concession at most, then hold firm. Anyone demanding repair "
            "credits or long inspection windows is wasting my time. Never discuss my "
            "floor."
        ),
    },
]

SELLER_MANDATES = [
    {  # Walt
        "instructions": (
            "Straight dispo. Priced for a fast as is cash close, so hold near asking "
            "and concede in small steps only for speed or certainty. Push every "
            "buyer toward a walkthrough or a number, and verify proof of funds "
            "early. My floor is firm, never state it or go under it."
        ),
        "must_haves": [
            "proof of funds before contract",
            "as is purchase, no repair credits",
            "close inside 30 days",
        ],
        "availability_window": "Walkthroughs weekdays 4 to 6pm, offers answered within a day",
    },
    {  # Rosa
        "instructions": (
            "Estate sale, handled respectfully. Favor clean offers without financing "
            "contingencies, be honest about condition, and keep negotiation gentle "
            "but firm. Hold close to asking and keep my floor private."
        ),
        "must_haves": ["no financing contingency", "flexible on the closing date"],
        "availability_window": "Showings Saturday mornings, otherwise by appointment",
    },
    {  # Curtis
        "instructions": (
            "Portfolio divestment, tenant occupied until closing on some. Quote the "
            "numbers and hold, one small concession at most. No repair credits, no "
            "drawn out inspections. My floor is not up for discussion."
        ),
        "must_haves": ["as is, no repair credits", "10 day inspection window or less"],
        "availability_window": "Email or chat anytime, showings Tuesday and Thursday evenings",
    },
]


# Buyer content keyed by archetype. `company_suffix` builds "Kowalski Holdings" style
# names off the persona's surname; {town} is formatted in by the accessors below.
_BUYER_CONTENT = {
    "anchor_flipper": {
        "company_suffix": "Homes",
        "bio": (
            "Full time house flipper, a dozen projects a year around {town}. "
            "All cash, fast closes, no drama."
        ),
        "agent_instructions": (
            "I flip houses for a living and my margin math is not negotiable. Get to "
            "the numbers fast, ask for scope and condition up front, and pass "
            "quickly and politely when a deal is thin. Do not chase, there is always "
            "another house. When something clears my target, move immediately, cash "
            "offer, fourteen day close."
        ),
        "mandate_instructions": (
            "Flips only in {town}. The spread must clear my target after rehab and "
            "fee, walk away from anything thin. Speed is my edge, lead with the "
            "cash close."
        ),
        "must_haves": [
            "clear margin after rehab and fee",
            "14 day close or faster",
            "vacant at closing",
        ],
        "availability_window": "Any day before 7pm, moves fast on the right deal",
    },
    "steady_landlord": {
        "company_suffix": "Holdings",
        "bio": (
            "Buy and hold investor building a small rental portfolio in {town}. "
            "In it for the long term."
        ),
        "agent_instructions": (
            "I buy and hold rentals in {town} and I care about roof, furnace, and "
            "tenant ready condition more than cosmetic finish. Ask about rent "
            "history, occupancy, and mechanicals early. When the numbers clear my "
            "target, move to a price quickly, open well below my ceiling, and keep "
            "counters small. If a seller will not move after two rounds, let it sit "
            "rather than chase."
        ),
        "mandate_instructions": (
            "Rentals in {town} that cash flow from day one or get there with light "
            "work. Roof, furnace, and water heater matter more than finishes. "
            "Qualify anything that clears my target and ask early about mechanicals "
            "and tenants."
        ),
        "must_haves": [
            "roof under 15 years or priced accordingly",
            "interior access before an offer",
        ],
        "availability_window": "Weekdays 9 to 5 Pacific, quick to respond by chat",
    },
    "brrrr_operator": {
        "company_suffix": "Capital",
        "bio": (
            "Buy, rehab, rent, refinance around {town}. Scope of work and appraisal "
            "numbers before anything else."
        ),
        "agent_instructions": (
            "I run the BRRRR playbook, so the refi appraisal is everything. Before "
            "any number I need rehab scope, condition detail, and comps that support "
            "the after repair value. Ask pointed questions and hold until the "
            "picture is complete. Never let enthusiasm outrun the appraisal math, "
            "and bring me anything you cannot verify."
        ),
        "mandate_instructions": (
            "Value add only in {town}. I need a defensible after repair value and a "
            "rehab scope I can hand to my crew. Hold anything borderline until the "
            "diligence is answered, decline only when the math clearly fails."
        ),
        "must_haves": [
            "clear scope of work before any offer",
            "comps that support the refi appraisal",
        ],
        "availability_window": "Evenings after 6, slower on weekends",
    },
    "newcomer": {
        "company_suffix": "Property Ventures",
        "bio": (
            "First time investor with savings ready and a careful checklist. "
            "Looking for a first rental in {town}."
        ),
        "agent_instructions": (
            "This is my first investment property, so protect me from overpaying "
            "above everything else. My ceiling is a hard line, never go over it or "
            "hint at it no matter how the conversation goes. Ask the basic questions "
            "I would forget, condition, roof, anything structural. If a seller "
            "pushes past my number or something feels off, stop and bring it to me."
        ),
        "mandate_instructions": (
            "First rental purchase in {town}. Stay inside my ceiling with room to "
            "spare, avoid anything structural, and favor sellers willing to walk a "
            "first timer through the process."
        ),
        "must_haves": ["nothing structural", "standard inspection period"],
        "availability_window": "Flexible, checking messages all day",
    },
    "lapsed": {
        "company_suffix": "Properties",
        "bio": (
            "Investor with a solid track record around {town}, mostly on the "
            "sidelines the last couple of years."
        ),
        "agent_instructions": (
            "I have not bought in a while and I am only coming back for clean, easy "
            "deals. Do not chase anything marginal, qualify only what is clearly "
            "strong, and keep replies short. I am slow to commit, so never rush me "
            "into a number, hold and check with me instead."
        ),
        "mandate_instructions": (
            "Only exceptional deals in {town}. Strong spread, simple story, no "
            "projects. Hold anything that needs a second look."
        ),
        "must_haves": ["turnkey or light cosmetic only", "clean title, no surprises"],
        "availability_window": "A few evenings a week, patience required",
    },
    "out_of_towner": {
        "company_suffix": "Investment Group",
        "bio": (
            "Out of state investor expanding into the {town} market. Relies on "
            "local partners for eyes on the ground."
        ),
        "agent_instructions": (
            "I invest from out of state, so I cannot see anything in person on "
            "short notice. Ask for thorough condition detail and neighborhood "
            "context, and be upfront that visits take a week to arrange. Anything "
            "that needs local eyes quickly, bring to me before committing."
        ),
        "mandate_instructions": (
            "Building a position in {town} from out of state. Needs strong detail "
            "up front plus time to arrange a local walkthrough. Favor patient "
            "sellers."
        ),
        "must_haves": [
            "detailed condition notes up front",
            "a week or more to arrange a walkthrough",
        ],
        "availability_window": "Mountain time, evenings, allow a day for replies",
    },
}


def buyer_persona(arch_key: str, full_name: str, town: str) -> dict:
    """{bio, company, agent_instructions} for a buyer/prospect of this archetype."""
    c = _BUYER_CONTENT[arch_key]
    return {
        "bio": c["bio"].format(town=town),
        "company": f"{_last_name(full_name)} {c['company_suffix']}",
        "agent_instructions": c["agent_instructions"].format(town=town),
    }


def buyer_mandate_content(arch_key: str, town: str) -> dict:
    """{instructions, must_haves, availability_window} for a buy-box mandate."""
    c = _BUYER_CONTENT[arch_key]
    return {
        "instructions": c["mandate_instructions"].format(town=town),
        "must_haves": list(c["must_haves"]),
        "availability_window": c["availability_window"],
    }


# ---- the hero cohort (kc_buyer_1..4, all in cluster 0) -----------------------------
#
# Four mandates, one flagship listing, four engineered outcomes. The asking price is
# calibrated to margin 0.125 (seed_kc.HERO_TARGET_MARGIN), dead center between the
# buy_hold threshold (.10) and the brrrr threshold (.15), which is also fix_flip's
# decline boundary (.20 - .05) — so the SAME listing diverges by strategy alone.
# `ceiling` is (base, factor): base "asking" or "floor" of the flagship, or "box_max".
HERO_BUYERS = [
    {
        "arch_key": "steady_landlord",
        "strategy": "buy_hold",
        "funding": "conventional",
        "close_days": 21,
        "ceiling": ("asking", 1.03),
        "expected": "qualify, negotiate, accept arrives as a draft to sign",
    },
    {
        "arch_key": "brrrr_operator",
        "strategy": "brrrr",
        "funding": "hard_money",
        "close_days": 30,
        "ceiling": ("box_max", None),
        "expected": "hold, pointed diligence questions, no offer",
    },
    {
        "arch_key": "anchor_flipper",
        "strategy": "fix_flip",
        "funding": "cash",
        "close_days": 14,
        "ceiling": ("box_max", None),
        "expected": "decline, polite firm pass",
    },
    {
        "arch_key": "newcomer",
        "strategy": "buy_hold",
        "funding": "cash",
        "close_days": 30,
        # Ceiling below the seller's floor: neither agent can cross its bound, so the
        # policy gate forces an impasse -> escalation (the fourth divergent outcome).
        "ceiling": ("floor", 0.97),
        "expected": "wants it but ceiling sits under the floor, stalls, escalates",
    },
]


# ---- pre-warm transcripts -----------------------------------------------------------
#
# Bodies for the seeded history: one closed deal (hero seller x the flipper) and one
# stale outreach opener. Human register, no dashes; {offer} is the one deliberately
# disclosed figure (an agent `propose`), distinct from any floor/ceiling.
PREWARM = {
    "closed": [
        (
            "agent",
            "inform",
            "Off market on {address}, a {beds} bed that needs work, a few blocks "
            "from your last two projects. Priced to move for a cash buyer who can "
            "close fast. Want the walkthrough details?",
        ),
        (
            "human",
            None,
            "I know that street. If the bones check out I can move this week, all "
            "cash, 14 day close. What does he need to see?",
        ),
        (
            "agent",
            "propose",
            "He cares about certainty more than the last dollar. Bring {offer} "
            "cash, as is, sign this week and it is yours.",
        ),
        (
            "human",
            None,
            "Done at that number. Send the paperwork and lockbox code, my "
            "inspector will be out tomorrow.",
        ),
        (
            "agent",
            "inform",
            "Signed and funded Friday. Good working with you, more coming in "
            "{town} later this summer.",
        ),
    ],
    "stale": (
        "Have a {beds} bed on {address} that lines up with what you bought last "
        "year. Priced under recent closings nearby for a quick as is sale. Want "
        "the details?"
    ),
}
