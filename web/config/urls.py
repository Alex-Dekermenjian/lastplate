"""
URL configuration for the web frontend.

https://docs.djangoproject.com/en/5.2/topics/http/urls/
"""
from django.urls import path

from frontend import account_views, health, views

urlpatterns = [
    path("", views.home, name="home"),
    path("register/", account_views.register, name="register"),
    path("sign-in/", account_views.sign_in, name="sign-in"),
    path("sign-out/", account_views.sign_out, name="sign-out"),
    path("workspace/", account_views.workspace, name="workspace"),
    path("workspace/finish-setup/", account_views.finish_setup, name="finish-setup"),
    path("workspace/profile/", account_views.shop_profile, name="shop-profile"),
    path("health/live/", health.live, name="health-live"),
    path("health/ready/", health.ready, name="health-ready"),
]
