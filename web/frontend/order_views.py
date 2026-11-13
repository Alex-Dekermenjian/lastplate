"""LP-78: the customer's order page and "My orders".

Like Too Good To Go: after reserving, the customer always has a page to show
at the counter (the big pickup code), and can find every order later.

Orders come from the Reservations API, which only answers for the signed-in
customer's own orders. The restaurant's name, branch, pickup address and
phone were saved with the order at checkout (the README rule: the agreed
address never changes later), so the page works even when Marketplace is
down. Orders placed before LP-78 saved none of it: they show just the
restaurant and branch name, looked up once from Marketplace's public
restaurant API, and never a live address. This service never reads another
service's database.
"""

from decimal import Decimal, InvalidOperation

from django.shortcuts import render
from django.utils import timezone

from . import services
from .account_views import authenticated_call, customer_required
from .browse_views import lebanon_time, pickup_window
from .services import ServiceUnavailable

ORDERS_UNAVAILABLE = "Can't load your orders right now, please try again."
ACTIVE = "CONFIRMED"


def _money(value):
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0.00")


def _day(moment):
    return f"{moment:%a} {moment.day} {moment:%b}"


def _clock(moment):
    """24-hour, as on the rest of the site: "Thu 19 Nov, 19:15"."""
    return f"{_day(moment)}, {moment:%H:%M}" if moment else ""


def minutes_left(deadline, now=None):
    """Whole minutes until the deadline, or None once it has passed."""
    if deadline is None:
        return None
    seconds = (deadline - (now or timezone.now())).total_seconds()
    return int(seconds // 60) if seconds > 0 else None


def countdown_text(minutes):
    if minutes is None:
        return "Pick-up time has passed"
    if minutes < 60:
        return f"{minutes} min left"
    return f"{minutes // 60} h {minutes % 60} min left"


def order_for_display(order, place=None):
    """What the order pages show, from the Reservations API answer."""
    lines = order.get("lines") if isinstance(order.get("lines"), list) else []
    starts = [m for m in (lebanon_time(line.get("pickup_start")) for line in lines) if m]
    ends = [m for m in (lebanon_time(line.get("pickup_end")) for line in lines) if m]
    deadline = lebanon_time(order.get("pick_up_by"))
    created = lebanon_time(order.get("created_at"))
    active = order.get("status") == ACTIVE
    left = minutes_left(deadline) if active else None
    status_label = order.get("status_label") or ""
    if status_label == "Cancelled" and order.get("cancelled_by"):
        status_label = f"Cancelled by {order['cancelled_by'].lower()}"
    return {
        "ref": order.get("reservation_ref") or "",
        "pickup_code": order.get("pickup_code") or "",
        "status_label": status_label,
        "customer_phone": order.get("customer_phone", "") if active else "",
        "active": active,
        "items": [
            {"title": line.get("title") or "Food", "quantity": line.get("quantity") or 0,
             "unit_price": _money(line.get("unit_price")),
             "line_total": _money(line.get("unit_price")) * int(line.get("quantity") or 0)}
            for line in lines
        ],
        "items_summary": ", ".join(f"{line.get('quantity')} × {line.get('title') or 'Food'}" for line in lines),
        "total": _money(order.get("total_cash")),
        "pickup_window": pickup_window(min(starts), min(ends)) if starts and ends else "Unknown",
        "pick_up_by": _clock(deadline),
        "pick_up_by_time": f"{deadline:%H:%M}" if deadline else "",
        "pick_up_by_day": _day(deadline) if deadline else "",
        "deadline_iso": deadline.isoformat() if deadline else "",
        "time_passed": active and left is None,
        "countdown": countdown_text(left) if active else "",
        "date": _day(created) if created else "",
        "shop_id": order.get("shop_id"),
        "place": place or {},
    }


def _saved_place(order):
    return {
        "name": order.get("shop_name") or "",
        "branch": order.get("branch_name") or "",
        "address": order.get("pickup_address") or "",
        "phone": order.get("shop_phone") or "",
    }


def _place(order, cache):
    """Where to collect. Saved with the order; for orders placed before
    LP-78 only the names, looked up once (best effort, never an address)."""
    place = _saved_place(order)
    if place["name"]:
        return place
    shop_id = order.get("shop_id")
    if shop_id and shop_id not in cache:
        try:
            status, data = services.restaurant_page(shop_id)
        except ServiceUnavailable:
            status, data = None, None
        shop = data.get("shop") if status == 200 and isinstance(data, dict) else None
        cache[shop_id] = shop if isinstance(shop, dict) else None
    shop = cache.get(shop_id) or {}
    branches = shop.get("branches") if isinstance(shop.get("branches"), list) else []
    branch = next((b for b in branches if isinstance(b, dict) and b.get("id") == order.get("branch_id")), {})
    return {
        "name": shop.get("name") or "Restaurant",
        "branch": (branch.get("name") or "") if len(branches) > 1 else "",
        "address": "",
        "phone": "",
    }


def _unavailable(request, template, extra=None):
    return render(request, template, {"unavailable": ORDERS_UNAVAILABLE, **(extra or {})}, status=503)


@customer_required
def my_orders(request):
    template = "frontend/my_orders.html"
    try:
        status, data = authenticated_call(request, services.my_orders)
    except ServiceUnavailable:
        return _unavailable(request, template)
    if status != 200 or not isinstance(data, dict) or not isinstance(data.get("orders"), list):
        return _unavailable(request, template)

    cache = {}
    orders = [order_for_display(o, _place(o, cache)) for o in data["orders"] if isinstance(o, dict)]
    return render(request, template, {
        "active_orders": [o for o in orders if o["active"]],
        "past_orders": [o for o in orders if not o["active"]],
    })


@customer_required
def order_detail(request, reservation_ref):
    template = "frontend/order_detail.html"
    try:
        status, data = authenticated_call(request, services.get_order, reservation_ref)
    except ServiceUnavailable:
        return _unavailable(request, template)
    if status == 404:
        return render(request, template, {"not_found": True}, status=404)
    if status != 200 or not isinstance(data, dict):
        return _unavailable(request, template)

    return render(request, template, {"order": order_for_display(data, _place(data, {}))})
