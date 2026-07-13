"""
sync_prompts — push the code prompt registry to Langfuse (bootstrap + drift report).

Langfuse is the runtime source of truth for prompts; the constants in
`polaris_agent.prompts` are the permanent code fallbacks. This command makes a
fresh Langfuse project one command away and keeps drift visible:

  * missing in Langfuse         -> created (fragments first, so composability
                                   tags resolve when the composed surfaces land)
  * identical to code           -> skipped
  * differs from code (UI edit) -> reported ONLY. `--update` appends a NEW
                                   version (history preserved, never overwrites);
                                   `--promote` also moves the label to it.

Never destructive: existing versions and labels are left alone unless
explicitly promoted.

Comparison note: the SDK returns prompts with composability tags already
resolved, so a composed surface is compared against its *flattened* code
rendering. A drifted fragment therefore also flags every surface that embeds
it — syncing the fragment brings both back.
"""

from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from polaris_agent import prompt_store


class Command(BaseCommand):
    help = "Create/refresh the Langfuse prompt library from the code registry (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--label",
            default=None,
            help="Deployment label to sync against (default: settings.LANGFUSE_PROMPT_LABEL).",
        )
        parser.add_argument(
            "--dry-run", action="store_true", help="Report what would happen; write nothing."
        )
        parser.add_argument(
            "--update",
            action="store_true",
            help="For drifted prompts, append a new (unlabeled) version from code.",
        )
        parser.add_argument(
            "--promote",
            action="store_true",
            help="With --update: also assign the label to the new version.",
        )

    def handle(self, *args, **opts):
        if not prompt_store.enabled():
            raise CommandError(
                "Langfuse is disabled — set LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY "
                "(and LANGFUSE_ENABLED != false) first."
            )
        label = opts["label"] or settings.LANGFUSE_PROMPT_LABEL
        dry_run, update, promote = opts["dry_run"], opts["update"], opts["promote"]
        client = prompt_store.langfuse_client()

        created = skipped = drifted = updated = 0
        for name, body in prompt_store.all_langfuse_bodies(label=label).items():
            # What the fetch should resolve to when Langfuse matches the code.
            expected = (
                prompt_store.FRAGMENTS[name]
                if name in prompt_store.FRAGMENTS
                else prompt_store.fallback_body(name)
            )
            try:
                current = client.get_prompt(name, label=label, type="text", cache_ttl_seconds=0)
            except Exception:  # noqa: BLE001 - not-found (or transient) => treat as missing
                current = None

            if current is None:
                created += 1
                if dry_run:
                    self.stdout.write(f"CREATE {name}  (dry-run: skipped)")
                else:
                    client.create_prompt(name=name, type="text", prompt=body, labels=[label])
                    self.stdout.write(self.style.SUCCESS(f"CREATE {name}  [{label}]"))
                continue

            if (current.prompt or "") == expected:
                skipped += 1
                continue

            drifted += 1
            if update and not dry_run:
                labels = [label] if promote else []
                client.create_prompt(name=name, type="text", prompt=body, labels=labels)
                updated += 1
                suffix = f"promoted to [{label}]" if promote else "unlabeled draft version"
                self.stdout.write(self.style.WARNING(f"UPDATE {name}  new version ({suffix})"))
            else:
                self.stdout.write(
                    self.style.WARNING(
                        f"DRIFT  {name}  differs from code (kept; --update pushes a new version)"
                    )
                )

        try:
            client.flush()
        except Exception:  # noqa: BLE001 - flush is best-effort
            pass

        self.stdout.write(
            f"done: {created} created, {skipped} in sync, {drifted} drifted"
            + (f", {updated} updated" if updated else "")
            + (" (dry-run — nothing written)" if dry_run else "")
        )
