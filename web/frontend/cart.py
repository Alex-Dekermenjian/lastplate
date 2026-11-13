"""LP-62: a customer's cart, from one restaurant at a time.

The cart lives only in the server-side session: no database table and no
reservation, so stock is not held until checkout (LP-19). Every add and
every cart page view re-reads each offer from Marketplace, so quantities
never go above what is left and sold-out offers show as no longer available.

LP-19: "Reserve" sends the cart to Reservations, which rechecks every item
and holds the stock. The cart page carries a request_key that stays the same
while the cart does, so a double click or a retry gives one order; any change
to the cart starts a new attempt with a new key.
"""

import uuid
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from . import services
from .account_views import authenticated_call, customer_required
from .browse_views import lebanon_time, offer_for_display
from .listing_photos import photo_url
from .services import ServiceUnavailable

CART_KEY = "cart"
UNAVAILABLE = "We can't reach the food service right now. Please try again in a few minutes."
NO_LONGER_AVAILABLE = "Sorry, that food is no longer available."
TRY_AGAIN = "We couldn't place your order. Nothing was reserved yet. Please press Reserve again."


def empty_cart():
    return {"shop_id": None, "shop_name": "", "shop_logo": "", "branch_id": None, "branch_name": "",
            "lines": {}, "prices": {}, "titles": {}}


def get_cart(request):
    """{"shop_id", "shop_name", "lines": {listing_id (str): quantity},
    "prices" and "titles": {listing_id: last seen}}, or an empty cart."""
    cart = request.session.get(CART_KEY)
    if not isinstance(cart, dict) or not cart.get("lines"):
        return empty_cart()
    return cart


def new_attempt(cart):
    """The cart changed: the next Reserve is a new checkout with a new key."""
    cart.pop("request_key", None)
    cart.pop("problems", None)


def request_key(cart):
    """The key for this cart's checkout, made when the cart page loads."""
    if not cart.get("request_key"):
        cart["request_key"] = str(uuid.uuid4())
    return cart["request_key"]


def save_cart(request, cart):
    if cart["lines"]:
        request.session[CART_KEY] = cart
    else:
        request.session.pop(CART_KEY, None)


def place_name(cart):
    """ "ZWZ · Hamra" for a restaurant with several branches, else "ZWZ"."""
    name = cart.get("shop_name") or ""
    return f"{name} · {cart['branch_name']}" if cart.get("branch_name") else name


def money(value):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


def cart_summary(request):
    """Item count and total for the cart bar, from the prices seen when adding."""
    cart = get_cart(request)
    count = sum(cart["lines"].values())
    total = sum((money(cart["prices"].get(key)) * qty for key, qty in cart["lines"].items()), Decimal("0"))
    return {"count": count, "total": total, "shop_id": cart["shop_id"], "shop_name": cart["shop_name"]}


def back_url(request, fallback="cart"):
    back = request.GET.get("back") or request.POST.get("back") or ""
    if back and url_has_allowed_host_and_scheme(back, allowed_hosts={request.get_host()}):
        return back
    return fallback


def fetch_offer(listing_id):
    """(status, offer) from the public offer API; offer is None unless 200."""
    status, data = services.listing_offer(listing_id)
    if status == 200 and isinstance(data, dict) and isinstance(data.get("shop"), dict):
        return status, data
    return status, None


@customer_required
def add_to_cart(request, listing_id):
    # A signed-out visitor is sent to login and comes back here with GET.
    if request.method != "POST":
        return redirect(back_url(request))

    try:
        status, offer = fetch_offer(listing_id)
    except ServiceUnavailable:
        messages.error(request, UNAVAILABLE)
        return redirect(back_url(request))
    if offer is None:
        messages.error(request, NO_LONGER_AVAILABLE if status == 404 else UNAVAILABLE)
        return redirect(back_url(request))

    cart = get_cart(request)
    shop = offer["shop"]
    # LP-68: a cart holds food from one branch. A cart saved before branches
    # (no branch) still only needs the same restaurant.
    branch = offer.get("branch") if isinstance(offer.get("branch"), dict) else {}
    other_branch = cart.get("branch_id") is not None and branch.get("id") is not None and cart["branch_id"] != branch["id"]
    if cart["lines"] and (cart["shop_id"] != shop.get("id") or other_branch):
        if request.POST.get("start_new") != "1":
            # One restaurant per cart: ask before clearing the other one.
            return render(request, "frontend/cart_switch.html", {
                "offer": offer_for_display(offer),
                "current_shop": place_name(cart),
                "back": back_url(request, ""),
            })
        cart = empty_cart()

    key = str(int(listing_id))
    free = int(offer.get("free_quantity") or 0)
    wanted = cart["lines"].get(key, 0) + 1
    if wanted > free:
        messages.info(request, f"Only {free} left of {offer.get('title') or 'this food'}.")
        wanted = free
    cart.update(
        shop_id=shop.get("id"), shop_name=shop.get("name") or "", shop_logo=shop.get("logo_display") or "",
        branch_id=branch.get("id"),
        # Named in the cart only when the restaurant has several branches.
        branch_name=(branch.get("name") or "") if (shop.get("branch_count") or 1) > 1 else "",
    )
    cart["lines"][key] = wanted
    cart["prices"][key] = str(offer.get("discounted_price"))
    cart.setdefault("titles", {})[key] = offer.get("title") or ""
    new_attempt(cart)
    save_cart(request, cart)
    if wanted:
        messages.success(request, f"Added {offer.get('title') or 'food'} to your cart.")
    return redirect(back_url(request))


@customer_required
@require_POST
def update_cart(request, listing_id):
    """− / + / remove for one line. Quantities stay between 1 and what's left."""
    cart = get_cart(request)
    key = str(int(listing_id))
    if key not in cart["lines"]:
        return redirect("cart")

    action = request.POST.get("action")
    if action == "remove":
        del cart["lines"][key]
        cart["prices"].pop(key, None)
    elif action == "decrease":
        cart["lines"][key] = max(cart["lines"][key] - 1, 1)
    elif action == "increase":
        try:
            _, offer = fetch_offer(listing_id)
        except ServiceUnavailable:
            messages.error(request, UNAVAILABLE)
            return redirect("cart")
        free = int(offer.get("free_quantity") or 0) if offer else 0
        if cart["lines"][key] + 1 > free:
            messages.info(request, f"Only {free} left." if free else NO_LONGER_AVAILABLE)
        else:
            cart["lines"][key] += 1
    if not cart["lines"]:
        cart = empty_cart()
    new_attempt(cart)
    save_cart(request, cart)
    return redirect("cart")


@customer_required
def view_cart(request):
    cart = get_cart(request)
    lines, total, unavailable = [], Decimal("0"), False

    for key, quantity in cart["lines"].items():
        try:
            status, offer = fetch_offer(key)
        except ServiceUnavailable:
            unavailable = True
            break
        free = int(offer.get("free_quantity") or 0) if offer else 0
        if not free:
            # Sold out, unpublished or pickup ended: greyed out, not counted.
            lines.append({"id": int(key), "title": cart.get("titles", {}).get(key, "This food"),
                          "quantity": quantity, "available": False})
            continue
        if quantity > free:
            # Someone else took some: never keep more than is left.
            quantity = cart["lines"][key] = free
            new_attempt(cart)
            messages.info(request, f"Only {free} left of {offer.get('title') or 'one item'}; we updated your cart.")
        cart["prices"][key] = str(offer.get("discounted_price"))
        cart.setdefault("titles", {})[key] = offer.get("title") or ""
        # LP-19: so a later "no longer available" can say "Pickup ended".
        cart.setdefault("ends", {})[key] = offer.get("pickup_end") or ""
        line_total = money(offer.get("discounted_price")) * quantity
        total += line_total
        lines.append({
            **offer_for_display(offer),
            "quantity": quantity,
            "line_total": line_total,
            "available": True,
            "at_most": quantity >= free,
        })

    problems = cart.get("problems", {})
    for line in lines:
        line["problem"] = problems.get(str(line["id"]), "")
    key = request_key(cart) if cart["lines"] else ""
    if not unavailable:
        save_cart(request, cart)

    return render(request, "frontend/cart.html", {
        "request_key": key,
        "has_gone": any(not line["available"] for line in lines),
        "cart": cart,
        # LP-66: carts saved before the logo was kept simply show the initial.
        "shop_logo": photo_url(cart.get("shop_logo")),
        "lines": lines,
        "total": total,
        "unavailable": unavailable,
    })


def _problem_text(cart, problem):
    """What to show on a cart line; "Pickup ended." when we know it did."""
    key = str(problem.get("listing_id"))
    if problem.get("code") == "not_available":
        end = lebanon_time(cart.get("ends", {}).get(key))
        if end is not None and end <= timezone.now():
            return "Pickup ended."
    return problem.get("detail") or NO_LONGER_AVAILABLE


def _apply_problems(request, cart, problems):
    """Show each problem on its line, and never keep more than is left."""
    shown = {}
    for problem in problems:
        key = str(problem.get("listing_id"))
        if key not in cart["lines"]:
            messages.error(request, problem.get("detail") or TRY_AGAIN)
            continue
        shown[key] = _problem_text(cart, problem)
        free = problem.get("free_quantity")
        if problem.get("code") == "only_n_left" and isinstance(free, int) and free > 0:
            cart["lines"][key] = min(cart["lines"][key], free)
        if problem.get("code") == "price_changed" and problem.get("unit_price"):
            cart["prices"][key] = str(problem["unit_price"])
    new_attempt(cart)
    cart["problems"] = shown


@customer_required
@require_POST
def checkout(request):
    """LP-19: Reserve. Reservations rechecks every item and holds the stock."""
    cart = get_cart(request)
    if not cart["lines"]:
        return redirect("cart")

    key = request.POST.get("request_key") or ""
    try:
        key = str(uuid.UUID(key))
    except ValueError:
        # An old page: reload the cart, which makes a fresh key.
        messages.info(request, "Your cart was updated. Please check it and press Reserve again.")
        return redirect("cart")

    lines = [{"listing_id": int(listing_id), "quantity": quantity,
              "expected_unit_price": cart["prices"].get(listing_id)}
             for listing_id, quantity in cart["lines"].items()]
    try:
        status, data = authenticated_call(request, services.create_reservation, key, lines)
    except ServiceUnavailable:
        # Unknown outcome: keep the same key, so pressing again continues it.
        messages.error(request, TRY_AGAIN)
        return redirect("cart")

    data = data if isinstance(data, dict) else {}
    if status in (200, 201) and data.get("pickup_code") and data.get("reservation_ref"):
        # LP-78: straight to the order page the customer shows at the counter.
        save_cart(request, empty_cart())
        return redirect("order-detail", reservation_ref=data["reservation_ref"])
    if status == 202:
        messages.warning(request, data.get("detail") or TRY_AGAIN)
        return redirect("cart")
    if status == 409 and data.get("code") == "cart_changed":
        _apply_problems(request, cart, data.get("problems") or [])
        save_cart(request, cart)
        messages.error(request, "Something in your cart changed, so nothing was reserved. Please check it below.")
        return redirect("cart")

    if data.get("code") in ("request_key_reused", "not_reserved"):
        new_attempt(cart)
        save_cart(request, cart)
    messages.error(request, data.get("detail") or TRY_AGAIN)
    return redirect("cart")
