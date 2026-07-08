# Data repair (2026-07-08): under the old semantics `escalate` set
# terminal='needs_decision', permanently killing auto-reply for the chat. Escalation is
# now a PAUSE (status='escalated' + escalated_for, no terminal) — clear the legacy
# terminals so pre-existing chats can resume once the awaited human returns.

from django.db import migrations


def _clear_legacy_terminals(apps, schema_editor):
    Chat = apps.get_model("chat", "Chat")
    Chat.objects.filter(terminal="needs_decision").update(terminal=None)


def _noop(apps, schema_editor):
    pass  # reverse: cannot know which chats had it; the old semantics are gone anyway


class Migration(migrations.Migration):

    dependencies = [
        ("chat", "0002_chat_escalated_for"),
    ]

    operations = [
        migrations.RunPython(_clear_legacy_terminals, _noop),
    ]
