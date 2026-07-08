from rest_framework.routers import DefaultRouter

from .views import DealViewSet

router = DefaultRouter()
router.register("deals", DealViewSet, basename="deal")

urlpatterns = router.urls
