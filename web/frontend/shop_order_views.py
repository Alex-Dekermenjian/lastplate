"""LP-42: thin owner order pages; all selection and ownership is in the API."""
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET

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
               "paid_tab": filters.get("status") == "COLLECTED"}
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
