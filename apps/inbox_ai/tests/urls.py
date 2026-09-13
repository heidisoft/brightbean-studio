from django.urls import include, path

from config.urls import urlpatterns as core_patterns

urlpatterns = [
    path("workspace/<uuid:workspace_id>/inbox/ai/", include("apps.inbox_ai.urls")),
    *core_patterns,
]
