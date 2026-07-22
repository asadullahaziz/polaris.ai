"""
run_evals — run the offline agent eval suite as Langfuse experiments (LIVE LLM).

This is the LLM-ful counterpart to the LLM-free pytest suite: it exercises the real
away-responder graph and the copilot's structured-output call against real models and
scores the results with the deterministic disclosure gates (+ a couple of judges).

Guards: Langfuse must be enabled (datasets are hosted there) AND a live provider key
must be present (the graph makes real model calls). Run inside the backend container
against a DISPOSABLE database (the responder seeds ephemeral chats and commits real
rows; `make down-v` resets).

  python manage.py run_evals --surface responder --limit 3 --run-name smoke
  python manage.py run_evals --surface all --run-name baseline
"""

from __future__ import annotations

import os
import uuid

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from polaris_agent import prompt_store


def _require_live_provider() -> None:
    provider = getattr(settings, "LLM_PROVIDER", "openrouter")
    key = "OPENROUTER_API_KEY" if provider == "openrouter" else "ANTHROPIC_API_KEY"
    if not os.environ.get(key):
        raise CommandError(
            f"No live provider key found ({key}). Evals make real model calls — set "
            f"{key} (LLM_PROVIDER={provider}). This is intentionally not run in `make test`."
        )


class Command(BaseCommand):
    help = "Run the offline agent eval suite as Langfuse experiments (needs a live provider key)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--surface",
            default="all",
            help="responder | screen | triage | copilot-extract | all (default: all)",
        )
        parser.add_argument(
            "--run-name",
            default=None,
            help="Experiment run name (a short unique suffix is appended). Use a stable "
            "prefix to compare runs across prompt/model changes.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Quick local smoke over the first N items (no hosted dataset run).",
        )

    def handle(self, *args, **opts):
        if not prompt_store.enabled():
            raise CommandError(
                "Langfuse is disabled — set LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY first "
                "(experiments and datasets are hosted in Langfuse)."
            )
        _require_live_provider()

        from evals import registry, runners

        surfaces = registry.resolve(opts["surface"])
        base = opts["run_name"] or "evals"
        suffix = uuid.uuid4().hex[:6]

        for key in surfaces:
            run_name = f"{base}-{key}-{suffix}"
            self.stdout.write(self.style.MIGRATE_HEADING(f"\n=== {key}  (run: {run_name}) ==="))
            result = runners.run_surface(key, run_name=run_name, limit=opts["limit"])
            self.stdout.write(_format_result(result))

        try:
            prompt_store.langfuse_client().flush()
        except Exception:  # noqa: BLE001 - flush is best-effort
            pass
        self.stdout.write(
            self.style.SUCCESS("\ndone — open the Langfuse Experiments UI to compare runs.")
        )


def _format_result(result) -> str:
    fn = getattr(result, "format", None)
    if callable(fn):
        try:
            # The SDK's format() emits literal "\n" sequences (upstream bug) — unescape.
            return fn().replace("\\n", "\n")
        except Exception:  # noqa: BLE001
            pass
    return str(result)
