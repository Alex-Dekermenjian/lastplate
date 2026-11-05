"""
URL configuration for the Reservations service.

https://docs.djangoproject.com/en/5.2/topics/http/urls/
"""
from django.urls import path

from config import health
from reservations import views

urlpatterns = [
    # LP-28: the restaurant accepts or rejects its new orders.
    path("api/shop/orders", views.shop_orders, name="shop-orders"),
    path("api/shop/orders/<uuid:ref>/accept", views.accept_order, name="shop-order-accept"),
    path("api/shop/orders/<uuid:ref>/reject", views.reject_order, name="shop-order-reject"),

    path("health/live/", health.LiveView.as_view(), name="health-live"),
    path("health/ready/", health.ReadyView.as_view(), name="health-ready"),
]
