"""LP-19: the Reservations API, mounted at /api/."""

from django.urls import path

from . import views
from .shop_views import shop_reservations
from . import collection_views
from . import shop_cancel_views
from . import strike_views
from . import demo_views
from . import notification_views
from . import timer_views
from .sales_views import sales

urlpatterns = [
    path("shop/sales", sales, name="shop-sales"),
    path("shop/sales/export", sales, {"export": True}, name="shop-sales-export"),
    path("shop/reservations/lookup", collection_views.lookup, name="pickup-lookup"),
    path("shop/reservations/<uuid:ref>/collect", collection_views.collect, name="collect-order"),
    path("shop/reservations/<uuid:ref>/undo", collection_views.collect, {"undo": True}, name="undo-order"),
    # LP-79: the restaurant cancels one order, or all of a branch's for today, with a reason.
    path("shop/reservations/<uuid:ref>/cancel", shop_cancel_views.shop_cancel, name="shop-cancel-order"),
    path("shop/reservations/cancel-all", shop_cancel_views.shop_cancel_all, name="shop-cancel-all"),
    path("admin/shop-cancellations", shop_cancel_views.shop_cancellations, name="shop-cancellations"),
    path("shop/reservations", shop_reservations, name="shop-reservations"),
    # LP-25: the web asks for late orders to expire whenever the offers list is shown.
    path("timers/expire", timer_views.expire, name="timers-expire"),
    # Demo clock only: orders from "the future" are deleted when the clock moves back.
    path("demo/rewind", demo_views.rewind, name="demo-rewind"),
    # LP-80: the admin sees customers' strikes and removes one with a reason.
    path("admin/customers", strike_views.customers, name="admin-customers"),
    path("admin/customers/<int:customer_user_id>", strike_views.customer, name="admin-customer"),
    path("admin/strikes/<uuid:ref>/remove", strike_views.remove, name="admin-remove-strike"),
    # LP-60: the admin blocks or unblocks a customer from ordering.
    path("admin/customers/<int:customer_user_id>/block", strike_views.block, name="admin-block-customer"),
    path("admin/customers/<int:customer_user_id>/unblock", strike_views.unblock, name="admin-unblock-customer"),
    # LP-41: the customer's in-app notifications.
    path("notifications", notification_views.notifications, name="notifications"),
    path("notifications/read", notification_views.mark_read, name="notifications-read"),
    path("reservations", views.reservations, name="reservations"),
    # LP-78: one of the customer's own orders.
    path("reservations/<uuid:ref>", views.reservation_detail, name="reservation-detail"),
    # LP-27: the customer cancels their own Reserved order before pick-up-by.
    path("reservations/<uuid:ref>/cancel", views.reservation_cancel, name="reservation-cancel"),
]
