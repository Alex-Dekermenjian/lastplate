"""LP-13: Public pages for browsing food offers. No sign-in needed.

Marketplace decides which offers are visible. These pages only format what
it returns: times in Lebanon local time, "Unknown" for missing ingredient or
allergen information, and safe photo links.
"""

from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from django.shortcuts import render
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_GET

from . import services
from .browse_search import FoodSearchForm, filter_offers, nearby_suggestion, place_label, type_label
from .listing_photos import photo_url
from .services import ServiceUnavailable

LEBANON = ZoneInfo("Asia/Beirut")

UNAVAILABLE_MESSAGE = (
    "We can't load food offers right now. Please try again in a few minutes."
)


def lebanon_time(value):
    """An API timestamp as an aware Asia/Beirut datetime, or None."""
    moment = parse_datetime(value) if isinstance(value, str) else None

    if moment is None or moment.tzinfo is None:
        return None

    return moment.astimezone(LEBANON)


def _day(moment):
    return f"{moment:%a} {moment.day} {moment:%b}"


def pickup_window(start, end):
    """For example "Thu 5 Nov, 16:00–18:00" (Lebanon time)."""
    if start is None or end is None:
        return "Unknown"

    if start.date() == end.date():
        return f"{_day(start)}, {start:%H:%M}–{end:%H:%M}"

    return f"{_day(start)}, {start:%H:%M} – {_day(end)}, {end:%H:%M}"


def discount_percent(original, discounted):
    """Whole-number saving, for example 56 for $8.00 down to $3.50, or None."""
    try:
        was, now = Decimal(str(original)), Decimal(str(discounted))
    except (InvalidOperation, TypeError, ValueError):
        return None

    if was <= 0 or now < 0 or now >= was:
        return None

    return int((was - now) * 100 / was)


def _known(value):
    if isinstance(value, str) and value.strip():
        return value.strip()
    return "Unknown"


def offer_for_display(data):
    """Only the fields the public pages show, ready for the template."""
    start = lebanon_time(data.get("pickup_start"))
    end = lebanon_time(data.get("pickup_end"))
    shop = data.get("shop") if isinstance(data.get("shop"), dict) else {}
    # LP-68: the branch the offer is picked up from. A restaurant with several
    # branches is shown as "ZWZ · Hamra"; with one, just "ZWZ".
    branch = data.get("branch") if isinstance(data.get("branch"), dict) else {}
    several_branches = (shop.get("branch_count") or 1) > 1

    # LP-38: A LastBag shows what it usually holds, never exact contents.
    is_lastbag = data.get("kind") == "LASTBAG"

    return {
        "id": data.get("id"),
        "is_lastbag": is_lastbag,
        "typical_contents": (data.get("typical_contents") or "").strip()
        if is_lastbag
        else "",
        "title": data.get("title") or ("Surprise bag" if is_lastbag else "Food offer"),
        "description": data.get("description") or "",
        "category": data.get("category") or "",
        "photo_url": photo_url(data.get("photo_display")),
        "ingredients": "" if is_lastbag else _known(data.get("ingredients_display")),
        "allergens": _known(data.get("allergens_display")),
        "original_price": data.get("original_price"),
        "discounted_price": data.get("discounted_price"),
        "free_quantity": data.get("free_quantity"),
        "pickup_address": data.get("pickup_address") or "",
        "pickup_window": pickup_window(start, end),
        "pickup_today": start is not None
        and start.date() == timezone.now().astimezone(LEBANON).date(),
        "discount_percent": discount_percent(
            data.get("original_price"), data.get("discounted_price")
        ),
        "terms_revision": data.get("terms_revision"),
        # LP-39: LastBags (LP-38) show typical contents, never exact ones.
        "is_lastbag": data.get("kind") == "LASTBAG",
        "typical_contents": data.get("typical_contents") or "",
        "shop": {
            "id": shop.get("id"),
            "name": shop.get("name") or "",
            # LP-68: the branch's area (where the food is), else the shop's.
            "area": branch.get("area") or shop.get("area") or "",
            "public_contact": shop.get("public_contact") or "",
            # LP-66: the round logo badge next to the restaurant's name.
            "logo_url": photo_url(shop.get("logo_display")),
        },
        "branch": {
            "id": branch.get("id"),
            "name": branch.get("name") or "",
            "label": (branch.get("name") or "") if several_branches else "",
        },
    }


@require_GET
def browse_offers(request):
    template = "frontend/browse_offers.html"

    try:
        status, data = services.browse_listings()
    except ServiceUnavailable:
        return render(
            request,
            template,
            {"unavailable": UNAVAILABLE_MESSAGE},
            status=503,
        )

    if status != 200 or not isinstance(data, list):
        return render(
            request,
            template,
            {"unavailable": UNAVAILABLE_MESSAGE},
            status=502,
        )

    offers = [
        offer_for_display(item)
        for item in data
        if isinstance(item, dict) and item.get("id") is not None
    ]

    return render(request, template, food_search_context(request, offers))


def food_search_context(request, offers):
    """LP-69: the search bar and the offers that fit it."""
    form = FoodSearchForm(request.GET)
    search = form.search()
    searching = any(search.values())
    shown = filter_offers(offers, search) if searching else offers
    return {
        "offers": shown,
        "search_form": form,
        "searching": searching,
        "search_place": place_label(search["place"]),
        "search_type": type_label(search["type"]),
        "total_offers": len(offers),
        "nearby": None if shown else nearby_suggestion(offers, search),
    }


def home_food(request):
    """LP-69: the home page shows the food straight away. If Marketplace is
    down the home page still opens, with a short message instead."""
    try:
        status, data = services.browse_listings()
    except ServiceUnavailable:
        return {"unavailable": UNAVAILABLE_MESSAGE}
    if status != 200 or not isinstance(data, list):
        return {"unavailable": UNAVAILABLE_MESSAGE}
    offers = [
        offer_for_display(item)
        for item in data
        if isinstance(item, dict) and item.get("id") is not None
    ]
    return food_search_context(request, offers)


@require_GET
def offer_detail(request, listing_id):
    template = "frontend/offer_detail.html"

    try:
        status, data = services.listing_offer(listing_id)
    except ServiceUnavailable:
        return render(
            request,
            template,
            {"unavailable": UNAVAILABLE_MESSAGE},
            status=503,
        )

    if status == 404:
        return render(request, template, {"not_found": True}, status=404)

    if status != 200 or not isinstance(data, dict):
        return render(
            request,
            template,
            {"unavailable": UNAVAILABLE_MESSAGE},
            status=502,
        )

    return render(request, template, {"offer": offer_for_display(data)})


@require_GET
def restaurant_detail(request, shop_id):
    """LP-39: a restaurant's page: its details, then its LastBag and items."""
    from .cart import cart_summary  # cart imports this module

    template = "frontend/restaurant.html"

    try:
        status, data = services.restaurant_page(shop_id)
    except ServiceUnavailable:
        return render(request, template, {"unavailable": UNAVAILABLE_MESSAGE}, status=503)

    if status == 404:
        return render(request, template, {"not_found": True}, status=404)

    if status != 200 or not isinstance(data, dict) or not isinstance(data.get("shop"), dict):
        return render(request, template, {"unavailable": UNAVAILABLE_MESSAGE}, status=502)

    shop = data["shop"]
    branches = [branch for branch in shop.get("branches") or [] if isinstance(branch, dict) and branch.get("id")]
    shop_card = {**shop, "branch_count": len(branches) or 1}
    offers = [
        offer_for_display({**item, "shop": shop_card})
        for item in data.get("offers") or []
        if isinstance(item, dict) and item.get("id") is not None
    ]

    # LP-68: the customer picks a branch and sees that branch's food. Offers
    # from before branches (no branch) belong to the main branch.
    chosen = next((b for b in branches if str(b["id"]) == request.GET.get("branch", "")), None)
    selected = chosen or (branches[0] if branches else None)
    if selected and len(branches) > 1:
        main_id = branches[0]["id"]
        offers = [
            offer for offer in offers
            if (offer["branch"]["id"] or main_id) == selected["id"]
        ]
    counts = {}
    for item in data.get("offers") or []:
        if isinstance(item, dict):
            branch_id = (item.get("branch") or {}).get("id") or (branches[0]["id"] if branches else None)
            counts[branch_id] = counts.get(branch_id, 0) + 1
    branch_tabs = [
        {**branch, "active": selected is not None and branch["id"] == selected["id"], "offer_count": counts.get(branch["id"], 0)}
        for branch in branches
    ] if len(branches) > 1 else []

    return render(request, template, {
        "shop": {
            "id": shop.get("id"),
            "name": shop.get("name") or "Restaurant",
            # LP-68: the chosen branch's address and area.
            "address": (selected or {}).get("address") or shop.get("address") or "",
            "area": (selected or {}).get("area") or shop.get("area") or "",
            "branch_name": (selected or {}).get("name", "") if branch_tabs else "",
            "public_contact": shop.get("public_contact") or "",
            "category": shop.get("category") or "",
            "photo_url": photo_url(shop.get("photo_display")),
            "logo_url": photo_url(shop.get("logo_display")),
        },
        "branch_tabs": branch_tabs,
        "lastbags": [offer for offer in offers if offer["is_lastbag"]],
        "items": [offer for offer in offers if not offer["is_lastbag"]],
        # LP-62: Add buttons for customers and signed-out visitors (who are
        # asked to log in first); restaurants and the admin never see them.
        "can_add": request.session.get("account", {}).get("role", "CUSTOMER") == "CUSTOMER",
        "cart": cart_summary(request),
    })