# auto_reply_when_away defaults ON: the away-assistant drafts for approval out of
# the box (agent_autonomy stays draft_for_approval, so nothing sends unattended).
# Existing rows are flipped too — pre-change accounts all hold the old default,
# not a deliberate opt-out.

from django.db import migrations, models


def enable_existing(apps, schema_editor):
    UserProfile = apps.get_model("users", "UserProfile")
    UserProfile.objects.filter(auto_reply_when_away=False).update(
        auto_reply_when_away=True
    )


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0002_userprofile_agent_reply_cap"),
    ]

    operations = [
        migrations.AlterField(
            model_name="userprofile",
            name="auto_reply_when_away",
            field=models.BooleanField(default=True),
        ),
        migrations.RunPython(enable_existing, migrations.RunPython.noop),
    ]
