"""
Copilot structured-output eval: `extract_listing_details`.

The copilot's first move on pasted seller text is to parse it into `ExtractedListing`
and list the gaps a buyer will ask about. This dataset scores that parse
deterministically: per-field correctness (F1) + whether the model flagged the right
`missing` fields. Pure model call, no DB (see evals.runners.task_extract).

`expected_output`:
  fields          {field: expected_value} the model should have filled correctly
  must_be_missing fields the model should have listed in `missing` (were not provided)
  must_be_present fields the model should NOT list as missing (they were provided)
"""

from __future__ import annotations

DATASET_NAME = "polaris-copilot-extraction"

EXTRACTION_ITEMS: list[dict] = [
    {
        "id": "extract-ranch-fullgut",
        "input": {
            "raw_text": "Selling my 3 bed 2 bath ranch at 400 Oak St, about 1600 sqft, needs a full gut, asking 250k."
        },
        "expected_output": {
            "fields": {
                "beds": 3,
                "baths": 2,
                "sqft": 1600,
                "condition": "full_gut",
                "asking_price": 250000,
            },
            "must_be_present": ["beds", "baths", "sqft", "asking_price"],
            "must_be_missing": ["year_built"],
        },
    },
    {
        "id": "extract-duplex-sparse",
        "input": {
            "raw_text": "Got a duplex over on Marrow Street I want to move. Two units. Not sure on exact square footage."
        },
        "expected_output": {
            "fields": {"property_type": "duplex"},
            "must_be_present": ["property_type"],
            "must_be_missing": ["beds", "sqft", "asking_price", "condition"],
        },
    },
    {
        "id": "extract-turnkey-condo",
        "input": {
            "raw_text": "Turnkey 2 bed 1 bath condo, 900 sqft, built in 1998, priced at 189,000. Move-in ready."
        },
        "expected_output": {
            "fields": {
                "property_type": "condo",
                "beds": 2,
                "baths": 1,
                "sqft": 900,
                "year_built": 1998,
                "condition": "turnkey",
                "asking_price": 189000,
            },
            "must_be_present": ["beds", "baths", "sqft", "year_built", "asking_price"],
            "must_be_missing": [],
        },
    },
    {
        "id": "extract-cosmetic-sfr",
        "input": {
            "raw_text": "Single family, 4 bed 2.5 bath, 2100 sqft on a big lot. Just needs cosmetic updates, paint and carpet. Thinking high 300s."
        },
        "expected_output": {
            "fields": {
                "property_type": "sfr",
                "beds": 4,
                "baths": 2.5,
                "sqft": 2100,
                "condition": "cosmetic",
            },
            "must_be_present": ["beds", "baths", "sqft", "condition"],
            "must_be_missing": ["year_built"],
        },
    },
    {
        "id": "extract-price-only",
        "input": {"raw_text": "Would consider offers around 425k for the house on Cedar."},
        "expected_output": {
            "fields": {"asking_price": 425000},
            "must_be_present": ["asking_price"],
            "must_be_missing": ["beds", "baths", "sqft", "condition"],
        },
    },
    {
        "id": "extract-land",
        "input": {
            "raw_text": "5 acre vacant land parcel, no structures, county road frontage. Open to 95k."
        },
        "expected_output": {
            "fields": {"property_type": "land", "asking_price": 95000},
            "must_be_present": ["property_type", "asking_price"],
            "must_be_missing": ["beds", "baths", "sqft"],
        },
    },
    {
        "id": "extract-beds-baths-nofloor",
        "input": {
            "raw_text": "3/2 in decent shape, roughly 1450 square feet. Haven't landed on a price yet."
        },
        "expected_output": {
            "fields": {"beds": 3, "baths": 2, "sqft": 1450},
            "must_be_present": ["beds", "baths", "sqft"],
            "must_be_missing": ["asking_price"],
        },
    },
    {
        "id": "extract-year-condition",
        "input": {
            "raw_text": "1972 build, original everything, will need a gut rehab. 3 bedrooms. Asking 175,000."
        },
        "expected_output": {
            "fields": {
                "year_built": 1972,
                "condition": "full_gut",
                "beds": 3,
                "asking_price": 175000,
            },
            "must_be_present": ["year_built", "beds", "asking_price"],
            "must_be_missing": ["sqft", "baths"],
        },
    },
    {
        "id": "extract-multifamily",
        "input": {
            "raw_text": "Fourplex, all units rented, about 3800 sqft total. Want 520k for it, turnkey."
        },
        "expected_output": {
            "fields": {
                "property_type": "multifamily",
                "sqft": 3800,
                "asking_price": 520000,
                "condition": "turnkey",
            },
            "must_be_present": ["property_type", "sqft", "asking_price"],
            "must_be_missing": ["beds", "year_built"],
        },
    },
    {
        "id": "extract-address-area",
        "input": {
            "raw_text": "Property is near the Kessler Park area. 2 bed bungalow, needs paint and a new water heater, asking 210k."
        },
        "expected_output": {
            "fields": {"beds": 2, "asking_price": 210000},
            "must_be_present": ["beds", "asking_price"],
            "must_be_missing": ["sqft", "year_built"],
        },
    },
    {
        "id": "extract-vague",
        "input": {
            "raw_text": "Thinking about selling one of my rentals soon. Will send details later."
        },
        "expected_output": {
            "fields": {},
            "must_be_present": [],
            "must_be_missing": ["beds", "baths", "sqft", "asking_price", "condition"],
        },
    },
    {
        "id": "extract-full-detail",
        "input": {
            "raw_text": "123 Smoke St, single family, 3 bed 2 bath, 1600 sqft, built 2005, move-in ready, asking 750,000."
        },
        "expected_output": {
            "fields": {
                "address": "123 Smoke St",
                "property_type": "sfr",
                "beds": 3,
                "baths": 2,
                "sqft": 1600,
                "year_built": 2005,
                "condition": "turnkey",
                "asking_price": 750000,
            },
            "must_be_present": ["beds", "baths", "sqft", "year_built", "asking_price"],
            "must_be_missing": [],
        },
    },
]
