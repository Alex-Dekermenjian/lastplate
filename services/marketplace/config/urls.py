
"""URL configuration for the Marketplace service."""

import re

from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path

from config import health
from marketplace.photo_effect_views import guarded_media

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
# LP-54: through a guard that never serves the original photo of a Blurred or
# "Blurred with magic" offer (customers only ever get its blurred copy).
if settings.DEBUG:
    urlpatterns += [
        re_path(
            r"^%s(?P<path>.*)$" % re.escape(settings.MEDIA_URL.lstrip("/")),
            guarded_media,
        ),
    ]
