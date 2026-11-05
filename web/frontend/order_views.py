"""LP-28: the restaurant's new orders, with Accept and Reject.

LP-23 will move this list onto the restaurant's "Today" screen; the views
and Reservations API stay the same.
"""

from uuid import UUID

from django.contrib import messages
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from . import services
from .account_views import authenticated_call, operator_required
from .browse_views import lebanon_time, pickup_window
from .services import ServiceUnavailable

UNAVAILABLE = "We can't reach the order service right now. Please try again in a few minutes."
UNCONFIRMED = (
    "We couldn't reach the order service, so we can't tell whether your decision was saved. "
    "Refresh the page to check before trying again."
)


def for_display(order):
    lines = [
        {**line, "pickup_window": pickup_window(lebanon_time(line.get("pickup_start")),
                                                lebanon_time(line.get("pickup_end")))}
        for line in order.get("lines") or []
    ]
    return {**order, "lines": lines, "placed_at": lebanon_time(order.get("created_at"))}


@operator_required
def new_orders(request):
    try:
        status, orders = authenticated_call(request, services.shop_orders)
    except ServiceUnavailable:
        status, orders = None, None
    return render(request, "frontend/shop_orders.html", {
        "orders": [for_display(order) for order in orders] if status == 200 else [],
        "unavailable": status != 200,
    })


@operator_required
@require_POST
def decide_order(request, order_ref):
    decision = request.POST.get("decision")
    reason = request.POST.get("reason", "").strip()
    try:
        ref = str(UUID(str(order_ref)))
    except ValueError:
        return redirect("shop-orders")

    if decision == "reject" and not reason:
        messages.error(request, "Give a reason for rejecting. The customer will see it.")
        return redirect("shop-orders")
    if decision not in ("accept", "reject"):
        return redirect("shop-orders")

    try:
        if decision == "accept":
            status, body = authenticated_call(request, services.accept_order, ref)
        else:
            status, body = authenticated_call(request, services.reject_order, ref, reason)
    except ServiceUnavailable:
        messages.warning(request, UNCONFIRMED)
        return redirect("shop-orders")

    if status == 200:
        if decision == "accept":
            messages.success(request, f"Order from {body.get('customer_name') or 'the customer'} accepted.")
        else:
            messages.success(request, "Order rejected. The customer will see your reason, and the food is back on sale.")
    elif status in (400, 409) and isinstance(body, dict) and body.get("detail"):
        # For example "This order was already accepted." after a double click.
        messages.info(request, body["detail"])
    elif status == 404:
        messages.error(request, "That order wasn't found.")
    else:
        messages.error(request, UNAVAILABLE)
    return redirect("shop-orders")
