
"""
URL configuration for the Marketplace service.
"""

from django.urls import include, path
from config import health

urlpatterns = [
    path(
        "health/live/",
        health.LiveView.as_view(),
        name="health-live",
    ),
    path(
        "health/ready/",
        health.ReadyView.as_view(),
        name="health-ready",
    ),
    path(
        "api/",
        include("marketplace.urls"),
    ),
]

