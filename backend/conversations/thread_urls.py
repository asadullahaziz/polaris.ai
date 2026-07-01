from rest_framework.routers import DefaultRouter

from .thread_views import ThreadViewSet

router = DefaultRouter()
router.register("threads", ThreadViewSet, basename="thread")

urlpatterns = router.urls
