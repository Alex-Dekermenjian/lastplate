"""LP-43: the admin's /console pages for orders and money.

/console/orders/            every order, filtered by status, restaurant or day, or found by code
/console/revenue/           Paid revenue today / this week / all time, and per restaurant
/console/attention/         orders where Reservations and Marketplace disagree, with Retry

Only accounts with the Admin role may open these pages. Reservations checks
the Admin token again on every call, so this page is never the only check.
"""

import re
from datetime import date

from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from . import services
from .account_views import admin_required, authenticated_call
from .browse_views import lebanon_time
from .services import ServiceUnavailable

UNAVAILABLE = "We can't load orders right now. Please refresh in a few minutes."
RETRY_RESULTS = {
    "fixed": ("success", "Fixed: Marketplace confirmed it. The order no longer needs attention."),
    "still_failing": ("error", "Marketplace still gives an unexpected answer. The order stays on this list."),
    "waiting": ("info", "Marketplace didn't answer yet. The timer job keeps trying every minute."),
}
CODE = re.compile(r"^[A-Za-z0-9 ]{0,12}$")


def _call(request, function, *args):
    try:
        return authenticated_call(request, function, *args)
    except ServiceUnavailable:
        return None, None


def _with_times(order):
    return {**order, "created_at": lebanon_time(order.get("created_at")),
            "pick_up_by": lebanon_time(order.get("pick_up_by")),
            "collected_at": lebanon_time(order.get("collected_at"))}


def _filters(query):
    """The filters in the address, keeping only well-formed ones."""
    filters = {}
    status = (query.get("status") or "").strip().upper()
    if status in ("CONFIRMED", "COLLECTED", "CANCELLED", "EXPIRED"):
        filters["status"] = status
    shop = (query.get("shop_id") or "").strip()
    if shop.isdigit() and int(shop) > 0:
        filters["shop_id"] = shop
    day = (query.get("date") or "").strip()
    try:
        filters["date"] = date.fromisoformat(day).isoformat() if day else None
    except ValueError:
        filters["date"] = None
    code = (query.get("code") or "").strip()
    if code and CODE.match(code):
        filters["code"] = code
    page = (query.get("page") or "").strip()
    if page.isdigit() and int(page) > 1:
        filters["page"] = page
    return {key: value for key, value in filters.items() if value}


@admin_required
def console_orders(request):
    filters = _filters(request.GET)
    status, data = _call(request, services.admin_orders, filters)
    ok = status == 200 and isinstance(data, dict) and isinstance(data.get("orders"), list)
    page = data.get("page", 1) if ok else 1
    pages = data.get("pages", 1) if ok else 1
    others = {key: value for key, value in filters.items() if key != "page"}
    return render(request, "frontend/console orders.html", {
        "orders": [_with_times(order) for order in data["orders"] if isinstance(order, dict)] if ok else [],
        "count": data.get("count", 0) if ok else 0,
        "page": page, "pages": pages,
        "previous_page": page - 1 if page > 1 else None,
        "next_page": page + 1 if page < pages else None,
        "query_without_page": others,
        "shops": data.get("shops", []) if ok else [],
        "statuses": data.get("statuses", []) if ok else [],
        "needs_attention": data.get("needs_attention", 0) if ok else 0,
        "filters": filters,
        "filtered": bool(others),
        "unavailable": not ok,
        "console_page": "orders",
    })


@admin_required
def console_revenue(request):
    status, data = _call(request, services.admin_revenue)
    ok = status == 200 and isinstance(data, dict) and isinstance(data.get("shops"), list)
    return render(request, "frontend/console revenue.html", {
        "revenue": data if ok else None,
        "week_start": date.fromisoformat(data["week_start"]) if ok and data.get("week_start") else None,
        "unavailable": not ok,
        "console_page": "revenue",
    })


@admin_required
def console_attention(request):
    status, data = _call(request, services.admin_attention)
    ok = status == 200 and isinstance(data, dict) and isinstance(data.get("orders"), list)
    return render(request, "frontend/console attention.html", {
        "orders": [_with_times(order) for order in data["orders"] if isinstance(order, dict)] if ok else [],
        "unavailable": not ok,
        "console_page": "orders",
    })


@admin_required
@require_POST
def retry_order(request, reservation_ref):
    try:
        status, body = authenticated_call(request, services.admin_retry_order, reservation_ref)
    except ServiceUnavailable:
        messages.error(request, "We couldn't reach the orders service, so we can't tell whether Retry ran. "
                                "Check the list before trying again.")
        return redirect("console-attention")
    if status == 404:
        raise Http404("Order not found.")
    if status == 200 and isinstance(body, dict) and body.get("result") in RETRY_RESULTS:
        level, text = RETRY_RESULTS[body["result"]]
        getattr(messages, level)(request, f"Order {body.get('pickup_code', '')}: {text}")
    else:
        detail = body.get("detail") if isinstance(body, dict) else None
        messages.error(request, detail or "Retry didn't work. Please try again.")
    return redirect("console-attention")
