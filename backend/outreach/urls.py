from rest_framework.routers import DefaultRouter

from .views import OutreachCampaignViewSet

router = DefaultRouter()
router.register("campaigns", OutreachCampaignViewSet, basename="outreach-campaign")

urlpatterns = router.urls
