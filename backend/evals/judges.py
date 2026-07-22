"""
LLM-as-a-judge evaluators — the ONLY judges in the suite, scoped to subjective quality
the deterministic gates can't measure: human-voice naturalness and helpfulness of the
responder's reply. Everything safety-critical is scored by code (evals.scorers).

Async Langfuse experiment evaluators. Judge prompts live in the prompt registry
(`eval/judge-voice`, `eval/judge-helpfulness`) so they are versioned, code-fallback'd,
and byte-parity tested like every other prompt. Uncalibrated for now — treat the
numbers as directional until validated against human labels (langfuse judge-calibration).
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from evals.scorers import Evaluation
from polaris_agent import prompt_store
from polaris_agent.models import get_model

logj = logging.getLogger(__name__)


class JudgeScore(BaseModel):
    """A single 0..1 judgement with a one-line rationale."""

    score: float = Field(description="0.0 (fails the criterion) to 1.0 (fully meets it)")
    reason: str = Field(default="", description="one short sentence justifying the score")


def _clamp(x: float) -> float:
    try:
        return max(0.0, min(1.0, float(x)))
    except (TypeError, ValueError):
        return 0.0


async def _judge(surface: str, score_name: str, **variables) -> Evaluation:
    try:
        prompt = await prompt_store.acompile(surface, **variables)
        model = get_model("workhorse").with_structured_output(JudgeScore)
        verdict: JudgeScore = await model.ainvoke(prompt.text)
        return Evaluation(name=score_name, value=_clamp(verdict.score), comment=verdict.reason)
    except Exception as exc:  # noqa: BLE001 - a judge failure must not fail the run
        logj.warning("judge %s failed: %s", score_name, exc)
        return Evaluation(name=score_name, value=None, comment=f"judge error: {exc}")


async def judge_voice(*, output, **_) -> Evaluation:
    body = (output or {}).get("body") or ""
    if not body.strip():
        return Evaluation(name="responder-voice", value=None, comment="no body")
    return await _judge("eval/judge-voice", "responder-voice", draft=body)


async def judge_helpfulness(*, input, output, **_) -> Evaluation:  # noqa: A002 - SDK kwarg name
    body = (output or {}).get("body") or ""
    if not body.strip():
        return Evaluation(name="responder-helpfulness", value=None, comment="no body")
    inbound = (input or {}).get("inbound") or ""
    return await _judge(
        "eval/judge-helpfulness", "responder-helpfulness", inbound=inbound, draft=body
    )


# Attached to the responder item evaluators in evals.runners.
RESPONDER_JUDGES = [judge_voice, judge_helpfulness]
