from rest_framework.routers import DefaultRouter

from .views import CopilotConversationViewSet

router = DefaultRouter()
router.register("conversations", CopilotConversationViewSet, basename="copilot-conversation")

urlpatterns = router.urls
