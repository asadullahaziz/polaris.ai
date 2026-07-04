# Generated for P4 (per-user away-agent reply cap — bounds the agent↔agent loop).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="agent_reply_cap",
            field=models.PositiveSmallIntegerField(default=3),
        ),
    ]
