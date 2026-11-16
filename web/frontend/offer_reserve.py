"""LP-83: a customer reserves one offer straight from its page.

At the bottom of the offer page a signed-in customer chooses a quantity (1 up
to how many are left) and presses Reserve. That uses the LP-19 checkout
(services.create_reservation) with one line, so every rule stays the same:
Reservations rechecks the offer, holds the stock, and allows at most 2 active
orders. Visitors, shop owners and admins only see the offer details.

The page carries a request_key made when it loads. It stays the same until
an order is placed or definitively refused, so a double click or "Press
Reserve again" gives one order. The cart and restaurant page are unchanged.
"""

import uuid

from django.shortcuts import redirect
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import services
from .account_views import ACCOUNT_KEY, CUSTOMER, authenticated_call, customer_required, signed_in
from .browse_views import lebanon_time
from .services import ServiceUnavailable

# What this session last saw of each offer page: its checkout key, the price
# shown and when pickup ends. Small; only the latest offers are kept.
SEEN_KEY = "offer_checkout"
# One message for the offer page, shown once at the bottom.
NOTICE_KEY = "offer_notice"
MAX_SEEN = 20
# Reservations accepts at most this many of one item per order.
MAX_QUANTITY = 100

TRY_AGAIN = "We couldn't place your order. Nothing was reserved yet. Please press Reserve again."
SOLD_OUT = "Sold out"
PICKUP_ENDED = "Pickup ended"


def is_customer(request):
    return signed_in(request) and request.session.get(ACCOUNT_KEY, {}).get("role") == CUSTOMER


def _seen(request):
    seen = request.session.get(SEEN_KEY)
    return seen if isinstance(seen, dict) else {}


def _save_seen(request, seen):
    # Keep only the most recent offers, so the session stays small.
    while len(seen) > MAX_SEEN:
        seen.pop(next(iter(seen)))
    request.session[SEEN_KEY] = seen


def _new_attempt(request, listing_id):
    """The next Reserve on this offer is a new order with a new key."""
    seen = _seen(request)
    entry = seen.get(str(listing_id))
    if entry:
        entry.pop("key", None)
        _save_seen(request, seen)


def _notice(request, listing_id, kind, text):
    request.session[NOTICE_KEY] = {"listing_id": int(listing_id), "kind": kind, "text": text}


def pop_notice(request, listing_id):
    notice = request.session.get(NOTICE_KEY)
    if isinstance(notice, dict) and notice.get("listing_id") == int(listing_id):
        return request.session.pop(NOTICE_KEY)
    return None


def _ended(when):
    moment = lebanon_time(when)
    return moment is not None and moment <= timezone.now()


def reserve_context(request, offer, data):
    """What the bottom of the offer page shows. `offer` is offer_for_display()."""
    if not is_customer(request):
        return {"can_reserve": False}

    free = int(data.get("free_quantity") or 0)
    if _ended(data.get("pickup_end")):
        return {"can_reserve": True, "reserve_closed": PICKUP_ENDED}
    if free <= 0:
        return {"can_reserve": True, "reserve_closed": SOLD_OUT}

    seen = _seen(request)
    key = str(offer["id"])
    entry = seen.pop(key, {})  # re-added last, so it counts as most recent
    entry.setdefault("key", str(uuid.uuid4()))
    entry["price"] = str(data.get("discounted_price"))
    entry["ends"] = data.get("pickup_end") or ""
    entry["title"] = offer["title"]
    seen[key] = entry
    _save_seen(request, seen)
    return {
        "can_reserve": True,
        "request_key": entry["key"],
        "quantities": range(1, min(free, MAX_QUANTITY) + 1),
    }


def closed_reason(request, listing_id):
    """For an offer page that can no longer be shown (Marketplace says 404):
    "Pickup ended" or "Sold out" if this customer saw the offer earlier."""
    if not is_customer(request):
        return None
    entry = _seen(request).get(str(listing_id))
    if not entry:
        return None
    return {"title": entry.get("title") or "", "reason": PICKUP_ENDED if _ended(entry.get("ends")) else SOLD_OUT}


def _problem_text(entry, problem):
    code = problem.get("code")
    if code == "not_available" and _ended(entry.get("ends")):
        return "Pickup ended."
    if code == "pickup_ended":
        return "Pickup ended."
    return problem.get("detail") or "This offer is no longer available."


@customer_required
@require_POST
def reserve_offer(request, listing_id):
    back = redirect("offer-detail", listing_id=listing_id)
    entry = _seen(request).get(str(int(listing_id)), {})

    key = request.POST.get("request_key") or ""
    try:
        key = str(uuid.UUID(key))
    except ValueError:
        _notice(request, listing_id, "info", "This page was out of date. Please check the offer and press Reserve again.")
        return back
    try:
        quantity = int(request.POST.get("quantity") or 0)
    except ValueError:
        quantity = 0
    if not 1 <= quantity <= MAX_QUANTITY:
        _notice(request, listing_id, "error", "Choose how many you want.")
        return back

    line = {"listing_id": int(listing_id), "quantity": quantity, "expected_unit_price": entry.get("price")}
    try:
        status, data = authenticated_call(request, services.create_reservation, key, [line])
    except ServiceUnavailable:
        # Unknown outcome: keep the key, so pressing again continues the same order.
        _notice(request, listing_id, "warning", TRY_AGAIN)
        return back

    data = data if isinstance(data, dict) else {}
    if status in (200, 201) and data.get("pickup_code") and data.get("reservation_ref"):
        # LP-78: straight to the order page the customer shows at the counter.
        _new_attempt(request, listing_id)
        return redirect("order-detail", reservation_ref=data["reservation_ref"])
    if status == 202:
        _notice(request, listing_id, "warning", data.get("detail") or TRY_AGAIN)
        return back
    if status == 409 and data.get("code") == "cart_changed":
        problems = [p for p in data.get("problems") or [] if isinstance(p, dict)]
        text = " ".join(_problem_text(entry, p) for p in problems) or "This offer changed. Nothing was reserved."
        _new_attempt(request, listing_id)
        _notice(request, listing_id, "error", f"{text} Nothing was reserved.")
        return back
    if data.get("code") in ("request_key_reused", "not_reserved"):
        _new_attempt(request, listing_id)
    _notice(request, listing_id, "error", data.get("detail") or TRY_AGAIN)
    return back
