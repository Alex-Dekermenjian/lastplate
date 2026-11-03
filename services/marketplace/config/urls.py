"""
URL configuration for the Marketplace service.

https://docs.djangoproject.com/en/5.2/topics/http/urls/
"""
from django.contrib import admin
from django.urls import include, path

from config import health

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("marketplace.urls")),
    path("health/live/", health.LiveView.as_view(), name="health-live"),
    path("health/ready/", health.ReadyView.as_view(), name="health-ready"),
]
