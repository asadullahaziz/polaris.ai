"""
sync_eval_datasets — push the code-defined eval datasets to Langfuse (idempotent).

The datasets in `evals.datasets` are the ground-truth source of truth (reviewed in
PRs). This mirrors `sync_prompts`: create-or-upsert, never destructive. Dataset items
carry a stable `id`, so re-running updates in place rather than duplicating.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from polaris_agent import prompt_store


class Command(BaseCommand):
    help = "Create/refresh the Langfuse eval datasets from code (idempotent upsert)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--surface",
            default="all",
            help="responder | screen | triage | copilot-extract | all (default: all)",
        )
        parser.add_argument(
            "--dry-run", action="store_true", help="Report what would be pushed; write nothing."
        )

    def handle(self, *args, **opts):
        if not prompt_store.enabled():
            raise CommandError(
                "Langfuse is disabled — set LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY "
                "(and LANGFUSE_ENABLED != false) first."
            )
        from evals import registry

        dry_run = opts["dry_run"]
        client = prompt_store.langfuse_client()
        surfaces = registry.resolve(opts["surface"])

        for key in surfaces:
            ds = registry.DATASETS[key]
            n = len(ds["items"])
            if dry_run:
                self.stdout.write(f"DATASET {ds['name']}  {n} items  (dry-run: skipped)")
                continue
            client.create_dataset(
                name=ds["name"], description=ds["description"], metadata=ds["metadata"]
            )
            for it in ds["items"]:
                client.create_dataset_item(
                    dataset_name=ds["name"],
                    id=it["id"],
                    input=it["input"],
                    expected_output=it["expected_output"],
                    metadata={"surface": key},
                )
            self.stdout.write(self.style.SUCCESS(f"DATASET {ds['name']}  {n} items upserted"))

        if not dry_run:
            try:
                client.flush()
            except Exception:  # noqa: BLE001 - flush is best-effort
                pass
        self.stdout.write("done" + (" (dry-run — nothing written)" if dry_run else ""))
