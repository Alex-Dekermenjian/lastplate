import re

from django.utils import timezone
from django.views.decorators.cache import never_cache
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .authentication import IsOperator
from .collection import CollectionRefused, UNDO_WINDOW, can_undo, pay_or_undo
from .marketplace_client import MarketplaceUnavailable, operator_shop
from .models import Order, PICKUP_CODE_REGEX, Status
from .views import order_data


def owned_shop(user_id):
    try:
        status, shop = operator_shop(user_id)
        if status == 404:
            raise CollectionRefused("not_found", "No order with this code here", 404)
        if status != 200 or not isinstance(shop, dict) or type(shop.get("id")) is not int \
                or not isinstance(shop.get("branches"), list) or any(
                    not isinstance(b, dict) or type(b.get("id")) is not int for b in shop["branches"]):
            raise MarketplaceUnavailable()
    except MarketplaceUnavailable:
        raise CollectionRefused("unavailable", "Can't reach the restaurant service. Please try again.", 503)
    return shop


def branch_from(data, shop):
    branch_id = data.get("branch_id")
    if type(branch_id) is not int or branch_id not in [b["id"] for b in shop["branches"]]:
        raise CollectionRefused("not_found", "No order with this code here", 404)
    return branch_id


def shop_order_data(order):
    return {**order_data(order), "customer_first_name": order.customer_first_name or "Customer",
            "collection_version": order.collection_version, "can_undo": can_undo(order),
            "undo_until": (order.collected_at + UNDO_WINDOW).isoformat() if order.collected_at else None}


@never_cache
@api_view(["POST"])
@permission_classes([IsAuthenticated, IsOperator])
def lookup(request):
    try:
        data = request.data if isinstance(request.data, dict) else {}
        shop = owned_shop(request.user.id)
        branch = branch_from(data, shop)
        code = data.get("pickup_code")
        code = code.strip().upper() if isinstance(code, str) else ""
        if not re.fullmatch(PICKUP_CODE_REGEX, code):
            raise CollectionRefused("not_found", "No order with this code here", 404)
        scoped = Order.objects.filter(shop_id=shop["id"], branch_id=branch, pickup_code=code)
        order = scoped.filter(status=Status.CONFIRMED).first() or scoped.filter(status=Status.EXPIRED).first()
        if order is None:
            raise CollectionRefused("not_found", "No order with this code here", 404)
        if order.status == Status.EXPIRED or timezone.now() >= order.pick_up_by:
            raise CollectionRefused("pickup_expired", "The pickup deadline has passed.",
                                    pick_up_by=order.pick_up_by.isoformat())
        return Response(shop_order_data(order))
    except CollectionRefused as error:
        return Response(error.body, status=error.status)


@never_cache
@api_view(["POST"])
@permission_classes([IsAuthenticated, IsOperator])
def collect(request, ref, undo=False):
    try:
        data = request.data if isinstance(request.data, dict) else {}
        shop = owned_shop(request.user.id)
        branch = branch_from(data, shop)
        version = data.get("version")
        if type(version) is not int or not 0 <= version < 2147483647:
            raise CollectionRefused("invalid_request", "A valid payment version is required.", 400)
        if not undo and data.get("cash_received") is not True:
            raise CollectionRefused("cash_required", "Confirm that the cash was received.", 400)
        order = pay_or_undo(ref, shop["id"], branch, version, undo=undo)
        return Response(shop_order_data(order))
    except CollectionRefused as error:
        return Response(error.body, status=error.status)
