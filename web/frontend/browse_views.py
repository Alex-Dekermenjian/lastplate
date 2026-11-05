"""LP-13: Public pages for browsing food offers. No sign-in needed.

Marketplace decides which offers are visible. These pages only format what
it returns: times in Lebanon local time, "Unknown" for missing ingredient or
allergen information, and safe photo links.
"""

from datetime import timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from django.shortcuts import render
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_GET

from . import services
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


def pickup_day(start, today=None):
    """"Today" or "Tomorrow" for a quick read on the cards, else ""."""
    if start is None:
        return ""
    today = today or timezone.localtime(timezone=LEBANON).date()
    if start.date() == today:
        return "Today"
    if start.date() == today + timedelta(days=1):
        return "Tomorrow"
    return ""


def discount_percent(original, discounted):
    """Whole-number saving, for example 56 for $8.00 → $3.50; None if unclear."""
    try:
        original, discounted = Decimal(str(original)), Decimal(str(discounted))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if original <= 0 or not 0 < discounted < original:
        return None
    return int((original - discounted) / original * 100)


def _known(value):
    if isinstance(value, str) and value.strip():
        return value.strip()
    return "Unknown"


def offer_for_display(data):
    """Only the fields the public pages show, ready for the template."""
    start = lebanon_time(data.get("pickup_start"))
    end = lebanon_time(data.get("pickup_end"))
    shop = data.get("shop") if isinstance(data.get("shop"), dict) else {}

    return {
        "id": data.get("id"),
        "title": data.get("title") or "Food offer",
        "description": data.get("description") or "",
        "category": data.get("category") or "",
        "photo_url": photo_url(data.get("photo_display")),
        "ingredients": _known(data.get("ingredients_display")),
        "allergens": _known(data.get("allergens_display")),
        "original_price": data.get("original_price"),
        "discounted_price": data.get("discounted_price"),
        "free_quantity": data.get("free_quantity"),
        "pickup_address": data.get("pickup_address") or "",
        "pickup_window": pickup_window(start, end),
        "pickup_day": pickup_day(start),
        "discount_percent": discount_percent(
            data.get("original_price"), data.get("discounted_price")
        ),
        "terms_revision": data.get("terms_revision"),
        "shop": {
            "name": shop.get("name") or "",
            "area": shop.get("area") or "",
            "public_contact": shop.get("public_contact") or "",
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

    return render(request, template, {"offers": offers})


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
