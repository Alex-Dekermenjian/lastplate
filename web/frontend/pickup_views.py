"""LP-24: server-side API calls; payment rules belong to Reservations."""
from uuid import UUID

from django.shortcuts import render
from django.views.decorators.http import require_http_methods, require_POST

from . import services
from .account_views import authenticated_call, operator_required
from .browse_views import lebanon_time
from .order_views import order_for_display
from .services import ServiceUnavailable
from .shop_order_views import load, owner_orders_gate


def integer(value):
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def result_page(request, status, data, **context):
    retry = context.pop("retry_candidate", None)
    if status == 200:
        try:
            if not isinstance(data, dict) or data.get("status") not in ("CONFIRMED", "COLLECTED") \
                    or type(data.get("collection_version")) is not int or data["collection_version"] < 0 \
                    or type(data.get("branch_id")) is not int:
                raise ValueError("Invalid order response")
            UUID(data["reservation_ref"])
            context["order"] = {**order_for_display(data),
                            "customer_first_name": data.get("customer_first_name") or "Customer",
                            "customer_phone": data.get("customer_phone") if data.get("status") == "CONFIRMED" else "",
                            "version": data.get("collection_version"), "branch_id": data.get("branch_id"),
                            "can_undo": data.get("can_undo", False)}
        except (KeyError, AttributeError, TypeError, ValueError, OverflowError):
            status, data = 503, None
    if status != 200:
        context["error"] = data.get("detail", "Can't load this order. Please try again.") if isinstance(data, dict) else "Can't load this order. Please try again."
        expiry = lebanon_time(data.get("pick_up_by")) if isinstance(data, dict) else None
        context["expiry"] = expiry
        if status == 503:
            context["retry"] = retry
    return render(request, "frontend/pickup.html", context, status=status)


@operator_required
@require_http_methods(["GET", "POST"])
def pickup(request):
    gate = owner_orders_gate(request)
    if gate is not None:
        return gate
    status, data = load(request, {"count_only": "1"})
    branches = data.get("branches", []) if status == 200 and isinstance(data, dict) else []
    if status != 200:
        return result_page(request, status if status in (403, 404) else 503, None)
    branch = request.POST.get("branch_id", request.GET.get("branch_id", ""))
    if request.method == "GET":
        return render(request, "frontend/pickup.html", {"branches": branches, "selected_branch": branch})
    payload = {"branch_id": integer(branch), "pickup_code": request.POST.get("pickup_code", "")}
    try:
        status, data = authenticated_call(request, services.pickup_lookup, payload)
    except ServiceUnavailable:
        status, data = 503, None
    return result_page(request, status, data, branches=branches, selected_branch=branch)


@operator_required
@require_POST
def payment(request, reservation_ref, undo=False):
    payload = {"branch_id": integer(request.POST.get("branch_id")),
               "version": integer(request.POST.get("version"))}
    if not undo:
        payload["cash_received"] = request.POST.get("cash_received") == "yes"
    try:
        status, data = authenticated_call(request, services.pickup_payment, reservation_ref, payload, undo)
    except ServiceUnavailable:
        status, data = 503, None
    # Preserve the original version on unknown outcomes; never auto-pay a fresh lookup.
    retry = {"ref": reservation_ref, **payload, "undo": undo}
    return result_page(request, status, data, retry_candidate=retry)
