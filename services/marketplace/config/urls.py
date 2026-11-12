
"""URL configuration for the Marketplace service."""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from config import health

urlpatterns = [
    # LP-9: Shop approval through Django Admin
    path("admin/", admin.site.urls),

    # Marketplace APIs
    path("api/", include("marketplace.urls")),

    # LP-20: stock API for the Reservations service (service key only)
    path("internal/", include("marketplace.internal_urls")),

    # Service health checks
    path("health/live/", health.LiveView.as_view(), name="health-live"),
    path("health/ready/", health.ReadyView.as_view(), name="health-ready"),
]

# LP-11: Serve uploaded photos during local development only.
if settings.DEBUG:
    urlpatterns += static(
        settings.MEDIA_URL,
        document_root=settings.MEDIA_ROOT,
    )
