"""LP-42: thin owner order pages; all selection and ownership is in the API."""
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from . import services
from .account_views import authenticated_call, operator_required, unapproved_shop_page
from .order_views import order_for_display
from .services import ServiceUnavailable


def owner_orders_gate(request):
    """Use the same setup/approval navigation as the restaurant workspace."""
    try:
        status, shop = authenticated_call(request, services.my_shop)
    except ServiceUnavailable:
        return None  # let the order API report availability, without guessing approval
    if status == 404:
        return redirect("restaurant-apply")
    if status == 200 and isinstance(shop, dict) and shop.get("approval_status"):
        destination = unapproved_shop_page(shop)
        if destination:
            return redirect(destination)
    return None


def load(request, filters):
    try:
        status, data = authenticated_call(request, services.shop_orders, filters)
    except ServiceUnavailable:
        return 503, None
    if status != 200 or not isinstance(data, dict) or not isinstance(data.get("orders"), list) \
            or not isinstance(data.get("branches"), list) or type(data.get("count")) is not int:
        return status if status in (403, 404) else 503, None
    if data["count"] < 0 or any(not isinstance(order, dict) for order in data["orders"]) or any(
        not isinstance(branch, dict) or type(branch.get("id")) is not int
        or not isinstance(branch.get("name"), str) for branch in data["branches"]
    ):
        return 503, None
    return status, data


@operator_required
@require_GET
def today_orders(request):
    gate = owner_orders_gate(request)
    if gate is not None:
        return gate
    filters = {key: request.GET.get(key, "") for key in ("q", "branch_id", "status")}
    if not filters["status"]:
        filters.pop("status")
    status, data = load(request, filters)
    context = {"query": filters["q"], "selected_branch": filters["branch_id"], "unavailable": data is None,
               "paid_tab": filters.get("status") == "COLLECTED", "cancel_reasons": CANCEL_REASONS}
    if data is not None:
        try:
            rows = [
                {**order_for_display(order), "customer_first_name": order.get("customer_first_name") or "Customer",
                 "collection_version": order.get("collection_version", 0), "can_undo": order.get("can_undo", False),
                 "branch_id": order.get("branch_id"),
                 "branch_name": order.get("branch_name") or next(
                     (b["name"] for b in data["branches"] if b["id"] == order.get("branch_id")), "Branch")}
                for order in data["orders"]
            ]
        except (AttributeError, TypeError, ValueError, OverflowError):
            # A broken upstream response should show the retry state, not a 500.
            context["unavailable"] = True
            status = 503
        else:
            context.update(branches=data["branches"], orders=rows)
    return render(request, "frontend/today_orders.html", context, status=status)


@operator_required
@require_GET
def orders_badge(request):
    status, data = load(request, {"count_only": "1"})
    return JsonResponse({"count": data["count"]} if data else {"detail": "Orders unavailable"}, status=status)


# LP-79: the reasons a restaurant may give; Reservations checks them too.
CANCEL_REASONS = ("Food ran out", "Closing early", "Other")
# The outcome is unknown (it may have gone through), so the reloaded list says which.
CANCEL_UNAVAILABLE = "We couldn't confirm the cancellation. Check the list below and try again if needed."


def _back_to_orders(request):
    branch = request.POST.get("return_branch", "")
    return redirect(f"{reverse('today-orders')}?branch_id={branch}" if branch.isdigit() else reverse("today-orders"))


def _report_refusal(request, status, data):
    if status in (400, 404, 409) and isinstance(data, dict) and isinstance(data.get("detail"), str):
        messages.error(request, data["detail"])
    else:
        messages.error(request, CANCEL_UNAVAILABLE)


@operator_required
@require_POST
def cancel_order(request, reservation_ref):
    """LP-79: cancel one Reserved order with a reason. Reservations decides
    (this shop's order, still Reserved, before pick-up-by); the food is
    removed from stock, not put back on sale."""
    try:
        status, data = authenticated_call(request, services.shop_cancel_order, reservation_ref,
                                          request.POST.get("reason", ""))
    except ServiceUnavailable:
        status, data = 503, None
    if status == 200 and isinstance(data, dict):
        code = data.get("pickup_code") if isinstance(data.get("pickup_code"), str) else ""
        messages.success(request, f"Order {code} is cancelled. The customer can see your reason." if code
                         else "The order is cancelled. The customer can see your reason.")
    else:
        _report_refusal(request, status, data)
    return _back_to_orders(request)


@operator_required
@require_POST
def cancel_all_today(request):
    """LP-79: cancel every Reserved order of one branch for today, with one reason."""
    try:
        branch_id = int(request.POST.get("branch_id", ""))
    except ValueError:
        branch_id = None
    try:
        status, data = authenticated_call(request, services.shop_cancel_all, branch_id,
                                          request.POST.get("reason", ""))
    except ServiceUnavailable:
        status, data = 503, None
    if status == 200 and isinstance(data, dict) and type(data.get("cancelled")) is int:
        count = data["cancelled"]
        messages.success(request, "No Reserved orders were left to cancel for today." if count == 0 else
                         f"{count} order{'' if count == 1 else 's'} cancelled. Customers can see your reason.")
    else:
        _report_refusal(request, status, data)
    return _back_to_orders(request)
