"""
URL configuration for the Reservations service.

https://docs.djangoproject.com/en/5.2/topics/http/urls/
"""
from django.contrib import admin
from django.urls import include, path

from config import health

urlpatterns = [
    # LP-17: team admins can look at orders and their history (never delete).
    path("admin/", admin.site.urls),
    # LP-19: customers place orders.
    path("api/", include("reservations.urls")),
    path("health/live/", health.LiveView.as_view(), name="health-live"),
    path("health/ready/", health.ReadyView.as_view(), name="health-ready"),
]
