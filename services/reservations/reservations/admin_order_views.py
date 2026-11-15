"""LP-43: the admin API for every order and the revenue (Accounts role ADMIN only).

GET  /api/admin/orders?status=&shop_id=&date=YYYY-MM-DD&code=&page=
                                     every order, newest first, 50 per page
GET  /api/admin/revenue              Paid revenue today / this week / all time,
                                     and per restaurant, highest first
GET  /api/admin/attention            orders where the services disagree
POST /api/admin/orders/{ref}/retry   redo the Marketplace step that failed

Customers and restaurants get 403; no token gets 401.
"""

from django.views.decorators.cache import never_cache
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from . import admin_orders
from .admin_orders import AdminRefused
from .strike_views import ADMIN_ONLY
from .timers import expire_due_orders_quietly


def _answer(function, *args):
    try:
        return Response(function(*args))
    except AdminRefused as refused:
        return Response(refused.body, status=refused.http_status)


@never_cache
@api_view(["GET"])
@permission_classes(ADMIN_ONLY)
def orders(request):
    expire_due_orders_quietly()
    return _answer(admin_orders.order_list, request.query_params)


@never_cache
@api_view(["GET"])
@permission_classes(ADMIN_ONLY)
def revenue(request):
    expire_due_orders_quietly()
    return _answer(admin_orders.revenue)


@never_cache
@api_view(["GET"])
@permission_classes(ADMIN_ONLY)
def attention(request):
    return _answer(admin_orders.attention)


@api_view(["POST"])
@permission_classes(ADMIN_ONLY)
def retry(request, ref):
    return _answer(admin_orders.retry, ref, request.user.id)
