"""
URL configuration for the web frontend.

https://docs.djangoproject.com/en/5.2/topics/http/urls/
"""
from django.urls import path

from frontend import health, views

urlpatterns = [
    path("", views.home, name="home"),
    path("health/live/", health.live, name="health-live"),
    path("health/ready/", health.ready, name="health-ready"),
]
