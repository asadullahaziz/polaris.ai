from django.apps import AppConfig


class OutreachConfig(AppConfig):
    """Outreach fan-out: outreach_campaign, outreach_recipient (delivery ledger) (P2)."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "outreach"
