# Generated for P4 (away-responder audit trail).

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ai", "0001_initial"),
        ("chat", "0001_initial"),  # AgentActionLog.chat → chat.Chat
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AgentActionLog",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                (
                    "action_type",
                    models.TextField(
                        choices=[
                            ("sent", "sent"),
                            ("drafted", "drafted"),
                            ("escalated", "escalated"),
                        ]
                    ),
                ),
                ("summary", models.TextField(blank=True, default="")),
                ("payload", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "principal",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="agent_actions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "chat",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="agent_actions",
                        to="chat.chat",
                    ),
                ),
            ],
            options={
                "db_table": "agent_action_log",
                "indexes": [
                    models.Index(
                        fields=["principal", "-created_at"], name="agent_action_principal_idx"
                    ),
                    models.Index(fields=["chat", "created_at"], name="agent_action_chat_idx"),
                ],
            },
        ),
    ]
