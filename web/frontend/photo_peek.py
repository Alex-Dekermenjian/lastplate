"""LP-54: photo effects on customer pages, the one-tap peek, and the owner's
real photo.

Marketplace decides what customers may see: for Blurred and Magic offers it
only ever sends the blurred copy's address. This module only passes the tap
on with the customer's token, puts saved peeks back on the page after a
reload, and fetches the real photo for its owner.
"""

import json
from urllib.parse import urlencode

from django.http import Http404, HttpResponse, JsonResponse
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from . import services
from .account_views import (
    ACCOUNT_KEY,
    CUSTOMER,
    SessionEnded,
    authenticated_call,
    operator_required,
    signed_in,
)
from .listing_photos import photo_url
from .services import ServiceUnavailable

NORMAL = "NORMAL"
BLURRED = "BLURRED"
MAGIC = "MAGIC"

# What the owner is told customers get.
EFFECT_LABELS = {
    NORMAL: "Normal photo",
    BLURRED: "Blurred",
    MAGIC: "Blurred with magic ✨",
}

PEEK_FIELDS = ("x", "y", "size", "image")


def photo_effect(value):
    return value if value in (BLURRED, MAGIC) else NORMAL


def is_customer(request):
    return signed_in(request) and request.session[ACCOUNT_KEY].get("role") == CUSTOMER


def _peek(data):
    """A saved peek as the page draws it, or None if it doesn't look right."""
    if not isinstance(data, dict) or not all(field in data for field in PEEK_FIELDS):
        return None
    image = data["image"]
    if not isinstance(image, str) or not image.startswith("data:image/png;base64,"):
        return None
    try:
        x, y, size = (float(data[field]) for field in ("x", "y", "size"))
    except (TypeError, ValueError):
        return None
    if not (0 <= x <= 100 and 0 <= y <= 100 and 0 < size <= 100):
        return None
    return {"x": x, "y": y, "size": size, "image": image}


def attach_peeks(request, offers):
    """Put this customer's saved peek on each Magic offer they peeked at, so
    a reload shows the same circle. One call for the whole page. If
    Marketplace can't answer, the page still opens; a tap then returns the
    saved peek anyway."""
    for offer in offers:
        offer.setdefault("peek", None)
    magic = [offer["id"] for offer in offers if offer.get("photo_effect") == MAGIC and offer.get("photo_url")]
    if not magic or not is_customer(request):
        return offers
    try:
        status, data = authenticated_call(request, services.my_peeks, magic)
    except (ServiceUnavailable, SessionEnded):
        return offers
    if status != 200 or not isinstance(data, list):
        return offers
    saved = {item.get("listing_id"): _peek(item) for item in data if isinstance(item, dict)}
    for offer in offers:
        if offer["id"] in saved:
            offer["peek"] = saved[offer["id"]]
    return offers


def _login_url(request, listing_id):
    back = request.headers.get("X-Peek-Page") or reverse("offer-detail", args=[listing_id])
    if not back.startswith("/") or back.startswith("//"):
        back = reverse("offer-detail", args=[listing_id])
    return f"{reverse('login')}?{urlencode({'next': back})}"


def _log_in_first(request, listing_id):
    return JsonResponse(
        {"code": "login", "detail": "Log in to peek.", "login_url": _login_url(request, listing_id)},
        status=401,
    )


@never_cache
@require_POST
def peek_offer(request, listing_id):
    """The page's tap: {"x": %, "y": %} of the photo. Answers with the circle
    to lay over the blurred photo, or why not."""
    if not signed_in(request):
        return _log_in_first(request, listing_id)
    if not is_customer(request):
        return JsonResponse({"code": "customers_only", "detail": "Only customers can peek."}, status=403)

    try:
        point = json.loads(request.body or b"{}")
    except ValueError:
        point = None
    if not isinstance(point, dict) or set(point) != {"x", "y"}:
        return JsonResponse({"code": "invalid", "detail": "Tap on the photo to peek."}, status=400)

    try:
        status, data = authenticated_call(request, services.peek_photo, listing_id, point["x"], point["y"])
    except SessionEnded:
        return _log_in_first(request, listing_id)
    except ServiceUnavailable:
        return JsonResponse(
            {"code": "unavailable", "detail": "We can't show your peek right now. Please try again."},
            status=503,
        )

    data = data if isinstance(data, dict) else {}
    if status == 201 and _peek(data):
        return JsonResponse({"peek": _peek(data)}, status=201)
    if status == 409 and data.get("code") == "already_peeked" and _peek(data.get("peek")):
        return JsonResponse(
            {"code": "already_peeked", "detail": "You've already had your one peek.", "peek": _peek(data["peek"])},
            status=409,
        )
    if status in (400, 404, 409):
        return JsonResponse(
            {"code": data.get("code") or "refused", "detail": data.get("detail") or "This photo can't be peeked at."},
            status=status,
        )
    return JsonResponse(
        {"code": "unavailable", "detail": "We can't show your peek right now. Please try again."},
        status=502,
    )


@operator_required
@require_GET
def owner_photo(request, listing_id):
    """The owner's real photo, even when customers only get a blurred one."""
    try:
        status, photo = authenticated_call(request, services.owner_listing_photo, listing_id)
    except ServiceUnavailable:
        return HttpResponse(status=502)
    if status != 200 or photo is None:
        raise Http404
    content_type, body = photo
    response = HttpResponse(body, content_type=content_type)
    # Only for the owner's own browser, never a shared cache.
    response["Cache-Control"] = "private, no-store"
    return response


def owner_photo_fields(listing):
    """How an owner's page shows an offer's photo: always the real one, and
    which effect customers get."""
    effect = photo_effect(listing.get("photo_effect"))
    if effect == NORMAL:
        src = photo_url(listing.get("photo_display"))
    else:
        src = reverse("owner-offer-photo", args=[listing["id"]]) if listing.get("photo_display") else None
    return {
        "photo_display": src,
        "photo_effect": effect,
        "photo_effect_label": EFFECT_LABELS[effect],
    }
