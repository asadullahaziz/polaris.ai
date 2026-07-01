from django.apps import AppConfig


class MatchingConfig(AppConfig):
    """
    Deterministic matching/ranking/comping engine over PostGIS (no domain
    tables of its own; matching_and_data §0 "engine scores, LLM narrates").

    P0 carries one throwaway geo fixture model (SpikePoint) purely to prove the
    GeoDjango/GDAL path; the real engine + tools land in P1/P2.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "matching"
