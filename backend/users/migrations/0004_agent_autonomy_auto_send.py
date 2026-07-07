# agent_autonomy defaults to auto_send: with auto_reply_when_away already ON by
# default (0003), the away-assistant now replies unattended out of the box, bounded
# by agent_reply_cap. Existing rows are flipped too — pre-change accounts all hold
# the old default, not a deliberate opt-down; users can opt back to
# draft_for_approval in settings.

from django.db import migrations, models


def auto_send_existing(apps, schema_editor):
    UserProfile = apps.get_model("users", "UserProfile")
    UserProfile.objects.filter(agent_autonomy="draft_for_approval").update(
        agent_autonomy="auto_send"
    )


def draft_existing(apps, schema_editor):
    UserProfile = apps.get_model("users", "UserProfile")
    UserProfile.objects.filter(agent_autonomy="auto_send").update(
        agent_autonomy="draft_for_approval"
    )


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0003_auto_reply_default_on"),
    ]

    operations = [
        migrations.AlterField(
            model_name="userprofile",
            name="agent_autonomy",
            field=models.CharField(
                choices=[
                    ("draft_for_approval", "draft_for_approval"),
                    ("auto_send", "auto_send"),
                ],
                default="auto_send",
                max_length=32,
            ),
        ),
        migrations.RunPython(auto_send_existing, draft_existing),
    ]
