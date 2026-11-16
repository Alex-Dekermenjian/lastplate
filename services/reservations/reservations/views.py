"""LP-19: the customer places an order from the cart.

POST /api/reservations  (customer access token)
    {"request_key": "<uuid>", "lines": [{"listing_id": 42, "quantity": 2,
                                        "expected_unit_price": "1.50"}]}

GET /api/reservations  (customer access token)          LP-78
    The customer's own orders, newest first, at most 50.
GET /api/reservations/{reservation_ref}  (customer token)  LP-78
    One order, only for the customer who made it; anyone else gets 404.
Both show only Reserved, Paid, Cancelled and Expired orders: PROCESSING and
NOT_RESERVED are never shown (plan §6).

Answers to POST:
- 201 a new Reserved order; 200 the same order again (same request_key);
- 202 {"code": "try_again"}: Marketplace gave no answer. Send the same
  request_key again to continue the same order;
- 409 {"code": "cart_changed", "problems": [...]}: nothing was reserved;
- 409 too_many_orders / request_key_reused / not_reserved, 403 customer_blocked
  or not a customer, 400 invalid_request, 503 when the cart could not even be
  checked (nothing saved).
"""

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .authentication import IsCustomer
from .cancellation import CancelRefused, cancel_by_customer
from .checkout import CheckoutRefused, TryAgain, place_order
from .models import Order, Status
from .blocks import active_block
from .strikes import strike_status
from .timers import expire_due_orders_quietly

# LP-78: how many orders "My orders" shows.
MAX_LISTED = 50


def order_data(order):
    return {
        "reservation_ref": str(order.reservation_ref),
        "status": order.status,
        "customer_phone": order.customer_phone if order.status == Status.CONFIRMED else "",
        "status_label": order.status_label,
        # LP-78: who cancelled ("Customer", "Restaurant", "Admin") and when it was paid.
        "cancelled_by": order.get_cancelled_by_display() if order.cancelled_by else None,
        # LP-79: why it was cancelled, so the customer sees the restaurant's reason.
        "cancel_reason": order.cancel_reason if order.status == Status.CANCELLED else "",
        "collected_at": order.collected_at.isoformat() if order.collected_at else None,
        "pickup_code": order.pickup_code,
        "pick_up_by": order.pick_up_by.isoformat(),
        "created_at": order.created_at.isoformat(),
        "total_cash": str(order.total_cash),
        "shop_id": order.shop_id,
        "branch_id": order.branch_id or None,
        # LP-78: saved at checkout; empty for orders placed before LP-78.
        "shop_name": order.shop_name,
        "branch_name": order.branch_name,
        "pickup_address": order.pickup_address,
        "shop_phone": order.shop_phone,
        "lines": [
            {
                "listing_id": line.listing_id,
                "title": line.title,
                "quantity": line.quantity,
                "unit_price": str(line.unit_price),
                "pickup_start": line.pickup_start.isoformat(),
                "pickup_end": line.pickup_end.isoformat(),
            }
            for line in order.lines.all()
        ],
    }


def customer_orders(request):
    """Only this customer's orders, and only the ones users may see."""
    return Order.objects.visible_to_users().filter(customer_user_id=request.user.id)


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated, IsCustomer])
def reservations(request):
    # LP-25: late orders expire before they are read or counted.
    expire_due_orders_quietly()
    if request.method == "GET":
        orders = customer_orders(request).prefetch_related("lines").order_by("-created_at", "-id")[:MAX_LISTED]
        # LP-80: the customer's strikes, for the warning banner. LP-60: and
        # whether the admin blocked ordering (until when; the reason stays internal).
        admin = active_block(request.user.id)
        return Response({"orders": [order_data(order) for order in orders],
                         "strikes": strike_status(request.user.id).as_dict(),
                         "admin_block": {"blocked_until": admin.blocked_until.isoformat()} if admin else None})

    data = request.data if isinstance(request.data, dict) else {}
    try:
        name = request.auth.get("display_name", "")
        first = name.split()[0][:150] if isinstance(name, str) and name.split() else ""
        phone = data.get("customer_phone", "")
        if not isinstance(phone, str) or len(phone) > 32 or any(c not in "+0123456789 ()-" for c in phone):
            return Response({"code": "invalid_request", "detail": "Enter a valid phone number (up to 32 characters)."}, status=400)
        result = place_order(request.user.id, data.get("request_key"), data.get("lines"),
                             customer_first_name=first, customer_phone=phone.strip())
    except CheckoutRefused as refused:
        body = {"code": refused.code, "detail": refused.detail}
        if refused.problems:
            body["problems"] = refused.problems
        return Response(body, status=refused.http_status)
    except TryAgain as again:
        # Saved but not confirmed (202), or not saved at all (503).
        return Response({"code": "try_again", "detail": again.detail},
                        status=status.HTTP_202_ACCEPTED if again.order else status.HTTP_503_SERVICE_UNAVAILABLE)

    return Response(order_data(result.order),
                    status=status.HTTP_201_CREATED if result.created else status.HTTP_200_OK)


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsCustomer])
def reservation_detail(request, ref):
    """LP-78: one order. Someone else's order is 404, never 403, so a
    reference reveals nothing about orders that are not yours."""
    expire_due_orders_quietly()  # LP-25
    order = customer_orders(request).prefetch_related("lines").filter(reservation_ref=ref).first()
    if order is None:
        return Response({"code": "not_found", "detail": "No such order."}, status=status.HTTP_404_NOT_FOUND)
    return Response(order_data(order))


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsCustomer])
def reservation_cancel(request, ref):
    """LP-27: POST /api/reservations/{ref}/cancel (customer token).

    200 the Cancelled order; 404 not one of your orders; 409 already_cancelled,
    already_paid, expired, or pickup_time_passed (with the shop's phone).
    """
    try:
        order = cancel_by_customer(ref, request.user.id)
    except CancelRefused as refused:
        return Response(refused.body, status=refused.http_status)
    return Response(order_data(order))
