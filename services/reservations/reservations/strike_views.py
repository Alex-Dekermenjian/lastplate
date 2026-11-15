"""LP-80: the admin API for strikes (Accounts role ADMIN only).

GET  /api/admin/customers                   customers with strikes or a running block
GET  /api/admin/customers/{customer_user_id} their strikes and expired-order history
POST /api/admin/strikes/{ref}/remove         {"reason": "..."}: remove one strike
LP-60:
POST /api/admin/customers/{id}/block         {"until": "YYYY-MM-DD", "reason": "..."}
POST /api/admin/customers/{id}/unblock

Customers and restaurants get 403; no token gets 401.
"""

from django.utils import timezone
from django.views.decorators.cache import never_cache
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .authentication import IsAdmin
from .blocks import active_block, block_customer, block_data, ordering_block, unblock_customer
from .models import CustomerBlock, Order, Status
from .strikes import STRIKE_WINDOW, StrikeRefused, customers_with_strikes, remove_strike, strike_status
from .timers import expire_due_orders_quietly

ADMIN_ONLY = [IsAuthenticated, IsAdmin]
# The expired-order history shown for one customer.
MAX_HISTORY = 100


def _customer(customer_user_id, first_name):
    until, why = ordering_block(customer_user_id)
    return {"customer_user_id": customer_user_id, "first_name": first_name,
            **strike_status(customer_user_id).as_dict(),
            # LP-60: the admin's block, and when ordering is possible again (either block).
            "admin_block": block_data(active_block(customer_user_id)),
            "ordering_blocked_until": until.isoformat() if until else None, "blocked_because": why}


def _first_name(customer_user_id):
    latest = Order.objects.filter(customer_user_id=customer_user_id).order_by("-created_at").first()
    return (latest.customer_first_name if latest else "") or "Customer"


@never_cache
@api_view(["GET"])
@permission_classes(ADMIN_ONLY)
def customers(request):
    expire_due_orders_quietly()
    rows = dict(customers_with_strikes())
    # LP-60: and customers the admin blocked, while the block runs.
    for block in CustomerBlock.objects.filter(lifted_at__isnull=True, blocked_until__gt=timezone.now()):
        rows.setdefault(block.customer_user_id, _first_name(block.customer_user_id))
    return Response({"customers": [_customer(*row) for row in rows.items()]})


def _expired_order(order, window_start):
    removal = getattr(order, "strike_removal", None)
    return {
        "reservation_ref": str(order.reservation_ref),
        "pickup_code": order.pickup_code,
        "pick_up_by": order.pick_up_by.isoformat(),
        "shop_name": order.shop_name,
        "branch_name": order.branch_name,
        "total_cash": str(order.total_cash),
        "items": [{"title": line.title, "quantity": line.quantity} for line in order.lines.all()],
        # Counts now: not removed, and within the last 30 days.
        "counts": removal is None and order.pick_up_by > window_start,
        "removed": None if removal is None else {
            "reason": removal.reason, "at": removal.created_at.isoformat(),
            "by_user_id": removal.removed_by_user_id},
    }


@never_cache
@api_view(["GET"])
@permission_classes(ADMIN_ONLY)
def customer(request, customer_user_id):
    expire_due_orders_quietly()
    orders = (Order.objects.filter(customer_user_id=customer_user_id, status=Status.EXPIRED)
              .select_related("strike_removal").prefetch_related("lines").order_by("-pick_up_by", "-id"))
    window_start = timezone.now() - STRIKE_WINDOW
    return Response({
        **_customer(customer_user_id, _first_name(customer_user_id)),
        "has_orders": Order.objects.filter(customer_user_id=customer_user_id).exists(),
        "expired_orders": [_expired_order(order, window_start) for order in orders[:MAX_HISTORY]],
    })


@api_view(["POST"])
@permission_classes(ADMIN_ONLY)
def block(request, customer_user_id):
    """LP-60: block a customer from ordering until a day, with a reason."""
    data = request.data if isinstance(request.data, dict) else {}
    try:
        block_customer(customer_user_id, data.get("until"), data.get("reason"), request.user.id)
    except StrikeRefused as refused:
        return Response(refused.body, status=refused.http_status)
    return Response(_customer(customer_user_id, _first_name(customer_user_id)))


@api_view(["POST"])
@permission_classes(ADMIN_ONLY)
def unblock(request, customer_user_id):
    try:
        unblock_customer(customer_user_id, request.user.id)
    except StrikeRefused as refused:
        return Response(refused.body, status=refused.http_status)
    return Response(_customer(customer_user_id, _first_name(customer_user_id)))


@api_view(["POST"])
@permission_classes(ADMIN_ONLY)
def remove(request, ref):
    data = request.data if isinstance(request.data, dict) else {}
    try:
        removal = remove_strike(ref, request.user.id, data.get("reason"))
    except StrikeRefused as refused:
        return Response(refused.body, status=refused.http_status)
    return Response({"reservation_ref": str(ref), "reason": removal.reason,
                     "customer": _customer(removal.order.customer_user_id, removal.order.customer_first_name)})
