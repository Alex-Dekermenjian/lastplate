"""LP-62: a customer's cart, from one restaurant at a time.

The cart lives only in the server-side session: no database table and no
reservation, so stock is not held until checkout (LP-19). Every add and
every cart page view re-reads each offer from Marketplace, so quantities
never go above what is left and sold-out offers show as no longer available.
"""

from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from . import services
from .account_views import customer_required
from .browse_views import offer_for_display
from .listing_photos import photo_url
from .services import ServiceUnavailable

CART_KEY = "cart"
UNAVAILABLE = "We can't reach the food service right now. Please try again in a few minutes."
NO_LONGER_AVAILABLE = "Sorry, that food is no longer available."


def empty_cart():
    return {"shop_id": None, "shop_name": "", "shop_logo": "", "lines": {}, "prices": {}, "titles": {}}


def get_cart(request):
    """{"shop_id", "shop_name", "lines": {listing_id (str): quantity},
    "prices" and "titles": {listing_id: last seen}}, or an empty cart."""
    cart = request.session.get(CART_KEY)
    if not isinstance(cart, dict) or not cart.get("lines"):
        return empty_cart()
    return cart


def save_cart(request, cart):
    if cart["lines"]:
        request.session[CART_KEY] = cart
    else:
        request.session.pop(CART_KEY, None)


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
    if cart["lines"] and cart["shop_id"] != shop.get("id"):
        if request.POST.get("start_new") != "1":
            # One restaurant per cart: ask before clearing the other one.
            return render(request, "frontend/cart_switch.html", {
                "offer": offer_for_display(offer),
                "current_shop": cart["shop_name"],
                "back": back_url(request, ""),
            })
        cart = empty_cart()

    key = str(int(listing_id))
    free = int(offer.get("free_quantity") or 0)
    wanted = cart["lines"].get(key, 0) + 1
    if wanted > free:
        messages.info(request, f"Only {free} left of {offer.get('title') or 'this food'}.")
        wanted = free
    cart.update(shop_id=shop.get("id"), shop_name=shop.get("name") or "", shop_logo=shop.get("logo_display") or "")
    cart["lines"][key] = wanted
    cart["prices"][key] = str(offer.get("discounted_price"))
    cart.setdefault("titles", {})[key] = offer.get("title") or ""
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
            messages.info(request, f"Only {free} left of {offer.get('title') or 'one item'}; we updated your cart.")
        cart["prices"][key] = str(offer.get("discounted_price"))
        cart.setdefault("titles", {})[key] = offer.get("title") or ""
        line_total = money(offer.get("discounted_price")) * quantity
        total += line_total
        lines.append({
            **offer_for_display(offer),
            "quantity": quantity,
            "line_total": line_total,
            "available": True,
            "at_most": quantity >= free,
        })

    if not unavailable:
        save_cart(request, cart)

    return render(request, "frontend/cart.html", {
        "cart": cart,
        # LP-66: carts saved before the logo was kept simply show the initial.
        "shop_logo": photo_url(cart.get("shop_logo")),
        "lines": lines,
        "total": total,
        "unavailable": unavailable,
    })
