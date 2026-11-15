"""LP-19: the Reservations API, mounted at /api/."""

from django.urls import path

from . import views
from .shop_views import shop_reservations
from . import collection_views
from . import strike_views
from . import timer_views
from . import admin_order_views

urlpatterns = [
    path("shop/reservations/lookup", collection_views.lookup, name="pickup-lookup"),
    path("shop/reservations/<uuid:ref>/collect", collection_views.collect, name="collect-order"),
    path("shop/reservations/<uuid:ref>/undo", collection_views.collect, {"undo": True}, name="undo-order"),
    path("shop/reservations", shop_reservations, name="shop-reservations"),
    # LP-25: the web asks for late orders to expire whenever the offers list is shown.
    path("timers/expire", timer_views.expire, name="timers-expire"),
    # LP-80: the admin sees customers' strikes and removes one with a reason.
    path("admin/customers", strike_views.customers, name="admin-customers"),
    path("admin/customers/<int:customer_user_id>", strike_views.customer, name="admin-customer"),
    path("admin/strikes/<uuid:ref>/remove", strike_views.remove, name="admin-remove-strike"),
    # LP-60: the admin blocks or unblocks a customer from ordering.
    path("admin/customers/<int:customer_user_id>/block", strike_views.block, name="admin-block-customer"),
    path("admin/customers/<int:customer_user_id>/unblock", strike_views.unblock, name="admin-unblock-customer"),
    # LP-43: every order, the revenue, and orders that need attention.
    path("admin/orders", admin_order_views.orders, name="admin-orders"),
    path("admin/orders/<uuid:ref>/retry", admin_order_views.retry, name="admin-retry-order"),
    path("admin/revenue", admin_order_views.revenue, name="admin-revenue"),
    path("admin/attention", admin_order_views.attention, name="admin-attention"),
    path("reservations", views.reservations, name="reservations"),
    # LP-78: one of the customer's own orders.
    path("reservations/<uuid:ref>", views.reservation_detail, name="reservation-detail"),
]
