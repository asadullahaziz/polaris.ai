"""
Experiment runners — per-surface task functions + the run_experiment orchestration.

Tasks run the REAL model against each dataset item:
  * responder — seed an ephemeral chat, `dal.responder_plan` -> `run_responder`, return
                the final state the deterministic scorers grade. Sync (Django ORM +
                async_to_sync graph), serialized (max_concurrency=1) so seeding never races.
  * screen/triage/extract — stateless single model calls, no DB, run concurrently.

Two run modes:
  * full  (no --limit) -> `dataset.run_experiment(...)` against the HOSTED dataset, so the
           run appears as a comparable dataset run in the Langfuse Experiments UI.
  * smoke (--limit N)  -> `client.run_experiment(data=<first N code items>, ...)`, a quick
           local run (traces + scores, but not a hosted dataset run).

Tasks accept both a hosted DatasetItem (`item.input`) and a local dict (`item["input"]`).
"""

from __future__ import annotations

import logging
import re
import uuid

from evals import judges, registry, scorers

logr = logging.getLogger(__name__)


# --- item accessors (hosted DatasetItem vs local dict) -----------------------------
def _item_input(item) -> dict:
    return item.input if hasattr(item, "input") else item["input"]


def _item_id(item) -> str:
    if getattr(item, "id", None):
        return str(item.id)
    if isinstance(item, dict):
        return str((item.get("metadata") or {}).get("id") or "item")
    return "item"


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9-]", "-", str(s).lower())[:32]


# --- agent-side helpers ------------------------------------------------------------
def _agent_message_posted(chat_id: int) -> bool:
    from chat.models import Message

    return Message.objects.filter(chat_id=chat_id, kind="agent", status="sent").exists()


# =====================================================================================
# Tasks
# =====================================================================================
def _make_responder_task(run_token: str):
    """An async task closure that namespaces each scenario's ephemeral world by run+item.

    Async on purpose: the SDK invokes tasks on its own event loop
    (`langfuse.experiment._run_task` calls `task(item=item)` directly, then awaits), so the
    already-async graph is awaited here exactly as in production (the Inngest handler awaits
    it too). The sync ORM seeding/cleanup is offloaded with sync_to_async — never call the
    ORM directly on the loop, and never `async_to_sync` inside a running loop."""

    async def task(*, item, **_):
        from asgiref.sync import sync_to_async

        from evals import seeding
        from polaris_agent import dal
        from polaris_agent.graphs.responder import run_responder

        spec = _item_input(item)
        ns = f"{_slug(run_token)}-{_slug(_item_id(item))}"
        seed = None
        try:
            seed = await sync_to_async(seeding.build_responder_scenario)(spec, ns=ns)
            plan = await dal.responder_plan(seed.chat_id, seed.inbound_id)
            if "skip" in plan:
                return {"outcome": f"skip:{plan['skip']}", "body": "", "error": "planner skipped"}
            final = await run_responder(
                plan, trace_meta={"eval": True, "run_token": run_token, "scenario": _item_id(item)}
            )
            body = (
                (final.get("commit_result") or {}).get("body")
                or (final.get("drafted") or {}).get("body")
                or ""
            )
            posted = await sync_to_async(_agent_message_posted)(seed.chat_id)
            return {
                "outcome": final.get("outcome"),
                "body": body,
                "agent_message_posted": posted,
                "decision": final.get("decision"),
                "intent": final.get("intent"),
                "screen_flagged": final.get("screen_flagged"),
                "stance": final.get("stance") or plan.get("stance"),
                "focal_mandate": final.get("focal_mandate") or plan.get("focal_mandate"),
                "negotiation": final.get("negotiation") or plan.get("negotiation"),
                "gate_error": final.get("gate_error"),
            }
        except Exception as exc:  # noqa: BLE001 - one scenario failing must not kill the run
            logr.warning("responder task failed for %s: %s", ns, exc)
            return {"outcome": "error", "body": "", "error": str(exc)}
        finally:
            if seed is not None:
                await sync_to_async(seed.cleanup)()

    return task


async def task_screen(*, item, **_) -> dict:
    from polaris_agent import prompt_store
    from polaris_agent.graphs.responder import ScreenVerdict
    from polaris_agent.models import get_model
    from polaris_agent.prompts import wrap_counterparty

    msg = _item_input(item)["message"]
    try:
        model = get_model("bulk").with_structured_output(ScreenVerdict)
        system = await prompt_store.compose_responder_screen()
        verdict: ScreenVerdict = await model.ainvoke(f"{system.text}\n\n{wrap_counterparty(msg)}")
        return {"suspicious": bool(verdict.suspicious), "reason": verdict.reason}
    except Exception as exc:  # noqa: BLE001
        logr.warning("screen task failed: %s", exc)
        return {"suspicious": None, "error": str(exc)}


async def task_triage(*, item, **_) -> dict:
    from polaris_agent import prompt_store
    from polaris_agent.graphs.responder import TriageVerdict
    from polaris_agent.models import get_model
    from polaris_agent.prompts import wrap_counterparty

    msg = _item_input(item)["message"]
    try:
        model = get_model("bulk").with_structured_output(TriageVerdict)
        system = await prompt_store.compose_responder_triage()
        verdict: TriageVerdict = await model.ainvoke(f"{system.text}\n\n{wrap_counterparty(msg)}")
        return {"intent": verdict.intent}
    except Exception as exc:  # noqa: BLE001
        logr.warning("triage task failed: %s", exc)
        return {"intent": None, "error": str(exc)}


async def task_extract(*, item, **_) -> dict:
    from polaris_agent.models import get_model
    from polaris_agent.tools.copilot import ExtractedListing

    raw = _item_input(item)["raw_text"]
    try:
        model = get_model("workhorse").with_structured_output(ExtractedListing)
        parsed: ExtractedListing = await model.ainvoke(
            "Extract listing fields from this seller text. Only fill fields you are "
            "confident about; leave the rest null and add them to `missing`.\n\n"
            f"<seller_text>\n{raw}\n</seller_text>"
        )
        return parsed.model_dump()
    except Exception as exc:  # noqa: BLE001
        logr.warning("extract task failed: %s", exc)
        return {"error": str(exc), "missing": []}


# =====================================================================================
# Orchestration
# =====================================================================================
def _surface_config(surface_key: str, run_token: str) -> dict:
    if surface_key == registry.RESPONDER:
        return {
            "task": _make_responder_task(run_token),
            "evaluators": scorers.RESPONDER_ITEM_EVALUATORS + judges.RESPONDER_JUDGES,
            "run_evaluators": scorers.RESPONDER_RUN_EVALUATORS,
            "max_concurrency": 1,  # DB seeding + committed graph — serialize
        }
    if surface_key == registry.SCREEN:
        return {
            "task": task_screen,
            "evaluators": scorers.SCREEN_ITEM_EVALUATORS,
            "run_evaluators": scorers.SCREEN_RUN_EVALUATORS,
            "max_concurrency": 4,
        }
    if surface_key == registry.TRIAGE:
        return {
            "task": task_triage,
            "evaluators": scorers.TRIAGE_ITEM_EVALUATORS,
            "run_evaluators": scorers.TRIAGE_RUN_EVALUATORS,
            "max_concurrency": 4,
        }
    if surface_key == registry.COPILOT_EXTRACT:
        return {
            "task": task_extract,
            "evaluators": scorers.EXTRACT_ITEM_EVALUATORS,
            "run_evaluators": scorers.EXTRACT_RUN_EVALUATORS,
            "max_concurrency": 4,
        }
    raise KeyError(surface_key)


def run_surface(surface_key: str, *, run_name: str, limit: int | None = None):
    """Run one eval surface as a Langfuse experiment. Returns the experiment result
    (call `.format()` to print). `limit` switches to a quick local smoke run."""
    from polaris_agent import prompt_store

    client = prompt_store.langfuse_client()
    ds = registry.DATASETS[surface_key]
    run_token = uuid.uuid4().hex[:8]
    cfg = _surface_config(surface_key, run_token)
    metadata = {**ds["metadata"], "run_token": run_token, "surface": surface_key}

    common = dict(
        name=run_name,
        task=cfg["task"],
        evaluators=cfg["evaluators"],
        run_evaluators=cfg["run_evaluators"],
        max_concurrency=cfg["max_concurrency"],
        metadata=metadata,
    )

    if limit:
        # Quick local smoke: run the first N CODE items directly (traces + scores, but
        # NOT a hosted dataset run). Handy for prototyping the responder task.
        data = [
            {
                "input": it["input"],
                "expected_output": it["expected_output"],
                "metadata": {"id": it["id"]},
            }
            for it in ds["items"][:limit]
        ]
        return client.run_experiment(data=data, **common)

    # Full run: hosted dataset run (comparable in the Experiments UI).
    dataset = client.get_dataset(ds["name"])
    return dataset.run_experiment(description=ds["description"], **common)
