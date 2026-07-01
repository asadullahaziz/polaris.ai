from django.contrib.gis.db import models as gis_models
from django.db import models


class SpikePoint(models.Model):
    """
    P0-ONLY geo fixture. Its whole purpose is to prove the risky integration
    seam (review #8): a GeoDjango `geography` column queried with `ST_DWithin`
    through the ORM, backed by GDAL/GEOS in the image and PostGIS in the DB.

    Replaced by the real domain geometry in P1 (`property.geom`, `buy_box_geo`).
    Safe to delete once P1 lands.
    """

    name = models.CharField(max_length=100)
    # geography=True -> distances are in metres, matching the domain's
    # buy-box geography model (data_model_decisions Decision 4).
    geom = gis_models.PointField(geography=True, srid=4326)

    class Meta:
        db_table = "spike_point"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.name
