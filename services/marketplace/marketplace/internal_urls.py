"""LP-20: internal endpoints, mounted at /internal/ (Reservations only)."""

from django.urls import path

from . import internal_views

urlpatterns = [
    path("shops/by-operator/<int:user_id>", internal_views.shop_by_operator, name="internal-shop-by-operator"),
    path("allocations", internal_views.allocations, name="internal-allocations"),
    path("allocations/<uuid:ref>", internal_views.allocation_detail, name="internal-allocation"),
    path("allocations/<uuid:ref>/release", internal_views.allocation_release, name="internal-allocation-release"),
    path("allocations/<uuid:ref>/consume", internal_views.allocation_consume, name="internal-allocation-consume"),
    path("allocations/<uuid:ref>/shop-cancel", internal_views.allocation_shop_cancel,
         name="internal-allocation-shop-cancel"),
]
