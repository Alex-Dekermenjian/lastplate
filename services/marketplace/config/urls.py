"""
URL configuration for the Marketplace service.

https://docs.djangoproject.com/en/5.2/topics/http/urls/
"""
from django.urls import path

from config import health

urlpatterns = [
    path("health/live/", health.LiveView.as_view(), name="health-live"),
    path("health/ready/", health.ReadyView.as_view(), name="health-ready"),
]
