"""LP-80: the admin API for strikes (Accounts role ADMIN only).

GET  /api/admin/customers                   customers with strikes or a running block
GET  /api/admin/customers/{customer_user_id} their strikes and expired-order history
POST /api/admin/strikes/{ref}/remove         {"reason": "..."}: remove one strike

Customers and restaurants get 403; no token gets 401.
"""

from django.utils import timezone
from django.views.decorators.cache import never_cache
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .authentication import IsAdmin
from .models import Order, Status
from .strikes import STRIKE_WINDOW, StrikeRefused, customers_with_strikes, remove_strike, strike_status
from .timers import expire_due_orders_quietly

ADMIN_ONLY = [IsAuthenticated, IsAdmin]
# The expired-order history shown for one customer.
MAX_HISTORY = 100


def _customer(customer_user_id, first_name):
    return {"customer_user_id": customer_user_id, "first_name": first_name,
            **strike_status(customer_user_id).as_dict()}


@never_cache
@api_view(["GET"])
@permission_classes(ADMIN_ONLY)
def customers(request):
    expire_due_orders_quietly()
    return Response({"customers": [_customer(*row) for row in customers_with_strikes()]})


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
    status = strike_status(customer_user_id)
    orders = (Order.objects.filter(customer_user_id=customer_user_id, status=Status.EXPIRED)
              .select_related("strike_removal").prefetch_related("lines").order_by("-pick_up_by", "-id"))
    latest = Order.objects.filter(customer_user_id=customer_user_id).order_by("-created_at").first()
    window_start = timezone.now() - STRIKE_WINDOW
    return Response({
        "customer_user_id": customer_user_id,
        "first_name": (latest.customer_first_name if latest else "") or "Customer",
        **status.as_dict(),
        "expired_orders": [_expired_order(order, window_start) for order in orders[:MAX_HISTORY]],
    })


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
