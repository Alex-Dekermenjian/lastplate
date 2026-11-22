"""LP-82: a customer's favourite restaurants.

The heart on a restaurant page saves or removes the restaurant, and the
Favourites page lists what those restaurants have left today. Marketplace
keeps the favourites and decides which offers are available (the same rules
as the public food list); this frontend only calls its API with the
customer's token and formats the answer.
"""

from django.contrib import messages
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET

from . import services
from .account_views import (
    ACCOUNT_KEY,
    CUSTOMER,
    SessionEnded,
    authenticated_call,
    customer_required,
    signed_in,
)
from .browse_views import offer_for_display
from .services import ServiceUnavailable

FAVOURITES_UNAVAILABLE = "Can't load your favourites right now, please try again."
HEART_UNAVAILABLE = "We couldn't update your favourites right now. Please try again."
RESTAURANT_GONE = "This restaurant isn't available, so it can't be saved."


def is_customer(request):
    return signed_in(request) and request.session.get(ACCOUNT_KEY, {}).get("role") == CUSTOMER


def is_favourite(request, shop_id):
    """Whether this signed-in customer saved the restaurant. Never breaks the
    restaurant page: if it can't be checked, the heart is shown empty."""
    if not is_customer(request):
        return False
    try:
        status, data = authenticated_call(request, services.favourite_shops)
    except (ServiceUnavailable, SessionEnded):
        return False
    if status != 200 or not isinstance(data, dict) or not isinstance(data.get("shops"), list):
        return False
    return any(isinstance(shop, dict) and shop.get("id") == shop_id for shop in data["shops"])


@customer_required
def toggle_favourite(request, shop_id):
    # A signed-out visitor who pressed the heart is sent to log in and comes
    # back here with GET. A GET changes nothing: it shows the page again.
    if request.method != "POST":
        return redirect("restaurant-detail", shop_id=shop_id)

    try:
        status, data = authenticated_call(request, services.toggle_favourite, shop_id)
    except ServiceUnavailable:
        messages.error(request, HEART_UNAVAILABLE)
        return redirect("restaurant-detail", shop_id=shop_id)

    if status == 404:
        messages.error(request, RESTAURANT_GONE)
    elif status != 200 or not isinstance(data, dict) or not isinstance(data.get("favourite"), bool):
        messages.error(request, HEART_UNAVAILABLE)
    elif data["favourite"]:
        messages.success(request, "Saved to your favourites.")
    else:
        messages.info(request, "Removed from your favourites.")
    return redirect("restaurant-detail", shop_id=shop_id)


@require_GET
@customer_required
def favourites(request):
    template = "frontend/favourites.html"
    try:
        status, data = authenticated_call(request, services.favourite_offers)
    except ServiceUnavailable:
        return render(request, template, {"unavailable": FAVOURITES_UNAVAILABLE}, status=503)
    if status != 200 or not isinstance(data, dict) or not isinstance(data.get("offers"), list):
        return render(request, template, {"unavailable": FAVOURITES_UNAVAILABLE}, status=502)

    offers = [
        offer_for_display(item)
        for item in data["offers"]
        if isinstance(item, dict) and item.get("id") is not None
    ]
    return render(request, template, {
        "has_favourites": bool(data.get("has_favourites")),
        "offers": offers,
    })
