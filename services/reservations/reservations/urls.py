"""LP-19: the Reservations API, mounted at /api/."""

from django.urls import path

from . import views
from .shop_views import shop_reservations
from . import collection_views

urlpatterns = [
    path("shop/reservations/lookup", collection_views.lookup, name="pickup-lookup"),
    path("shop/reservations/<uuid:ref>/collect", collection_views.collect, name="collect-order"),
    path("shop/reservations/<uuid:ref>/undo", collection_views.collect, {"undo": True}, name="undo-order"),
    path("shop/reservations", shop_reservations, name="shop-reservations"),
    path("reservations", views.reservations, name="reservations"),
    # LP-78: one of the customer's own orders.
    path("reservations/<uuid:ref>", views.reservation_detail, name="reservation-detail"),
]
