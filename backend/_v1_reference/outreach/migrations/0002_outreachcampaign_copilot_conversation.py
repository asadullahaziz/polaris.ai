import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("conversations", "0001_initial"),
        ("outreach", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="outreachcampaign",
            name="copilot_conversation",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="outreach_campaigns",
                to="conversations.conversation",
            ),
        ),
    ]
