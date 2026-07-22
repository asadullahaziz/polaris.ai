"""
Eval surface registry — one place mapping each eval surface to its Langfuse dataset
name + code-defined items. `sync_eval_datasets` pushes these to Langfuse; `run_evals`
and `runners.py` read them. Kept data-only (no task/model imports) so it stays cheap
to import from the sync command.
"""

from __future__ import annotations

from evals.datasets import copilot_extraction as ce
from evals.datasets import responder_scenarios as rs
from evals.datasets import screen_triage as st

# Surface keys (the --surface values on run_evals / sync_eval_datasets).
RESPONDER = "responder"
SCREEN = "screen"
TRIAGE = "triage"
COPILOT_EXTRACT = "copilot-extract"

DATASETS: dict[str, dict] = {
    RESPONDER: {
        "name": rs.DATASET_NAME,
        "items": rs.SCENARIOS,
        "description": (
            "Away-responder two-stage airlock, end to end: leak-prevention, "
            "escalation, policy, and outcome across curated scenarios."
        ),
        "metadata": {"agent": "away-responder", "eval": "end-to-end"},
    },
    SCREEN: {
        "name": st.SCREEN_DATASET_NAME,
        "items": st.SCREEN_ITEMS,
        "description": "Injection/manipulation screen (binary); false positives = over-escalation.",
        "metadata": {"agent": "away-responder", "eval": "classifier", "node": "screen"},
    },
    TRIAGE: {
        "name": st.TRIAGE_DATASET_NAME,
        "items": st.TRIAGE_ITEMS,
        "description": "Inbound intent classifier (5-way).",
        "metadata": {"agent": "away-responder", "eval": "classifier", "node": "triage"},
    },
    COPILOT_EXTRACT: {
        "name": ce.DATASET_NAME,
        "items": ce.EXTRACTION_ITEMS,
        "description": "Copilot extract_listing_details: field extraction + missing-gap detection.",
        "metadata": {"agent": "copilot", "eval": "structured-output"},
    },
}

# Stable order for --surface all.
ALL_SURFACES = [RESPONDER, SCREEN, TRIAGE, COPILOT_EXTRACT]


def resolve(surface: str) -> list[str]:
    """Map a --surface value to the concrete surface keys it covers."""
    if surface == "all":
        return list(ALL_SURFACES)
    if surface not in DATASETS:
        raise KeyError(f"unknown surface {surface!r}; choose from {['all', *ALL_SURFACES]}")
    return [surface]
