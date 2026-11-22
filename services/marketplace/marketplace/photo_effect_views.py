"""LP-54: the magic peek, the owner's real photo, and the guarded media route.

- POST /api/listings/{id}/peek          customer: one tap, saved for good
- GET  /api/listings/{id}/peek          customer: the saved peek (reloads)
- GET  /api/peeks/mine?listings=1,2,3   customer: saved peeks for a page
- GET  /api/listings/{id}/photo/original  owner: the real photo, any effect
- /media/...  (development only) every upload except the original of a
  Blurred or Magic offer
"""

import mimetypes
import re
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import IntegrityError, transaction
from django.http import FileResponse, Http404
from django.views.static import serve
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response

from .authentication import IsShopOperator
from .browse import visible_listing
from .models import Listing, PhotoPeek, Shop
from .photo_effects import make_peek, peek_payload

MAGIC = Listing.PhotoEffect.MAGIC
NORMAL = Listing.PhotoEffect.NORMAL
MAX_LISTINGS_PER_LOOKUP = 100

# One file, directly inside one of the folders upload_paths.py writes to.
MEDIA_FILE = re.compile(
    r"(?P<folder>listing_photos|listing_photos_blurred|shop_photos|shop_logos)"
    r"/[A-Za-z0-9_-]+\.(?:jpg|jpeg|png)",
    re.IGNORECASE,
)


class IsCustomer(BasePermission):
    message = "Only customers can peek."

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.role == "CUSTOMER")


def _error(code, detail, http_status, **extra):
    return Response({"code": code, "detail": detail, **extra}, status=http_status)


def _percentage(value):
    """A number from 0 to 100 (two decimals), or None."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    if not number.is_finite() or not 0 <= number <= 100:
        return None
    return number.quantize(Decimal("0.01"))


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated, IsCustomer])
def listing_peek(request, pk):
    if request.method == "GET":
        peek = PhotoPeek.objects.filter(
            listing_id=pk, customer_user_id=request.user.id, listing__photo_effect=MAGIC,
        ).first()
        if peek is None:
            return _error("no_peek", "You haven't peeked at this photo yet.", status.HTTP_404_NOT_FOUND)
        return Response(peek_payload(peek))

    if not isinstance(request.data, dict) or set(request.data) != {"x", "y"}:
        return _error("invalid", "Send the tap point as x and y.", status.HTTP_400_BAD_REQUEST)
    x, y = _percentage(request.data["x"]), _percentage(request.data["y"])
    if x is None or y is None:
        return _error(
            "invalid", "x and y are percentages of the photo, from 0 to 100.", status.HTTP_400_BAD_REQUEST,
        )

    # Only offers customers can see right now can be peeked at.
    visible = visible_listing(pk)
    if visible is None:
        return _error("not_found", "This offer is not available.", status.HTTP_404_NOT_FOUND)
    listing, _shop = visible
    if listing.photo_effect != MAGIC or not listing.photo:
        return _error("no_magic", "This photo can't be peeked at.", status.HTTP_409_CONFLICT)

    existing = PhotoPeek.objects.filter(listing=listing, customer_user_id=request.user.id).first()
    if existing is not None:
        return _already_peeked(existing)

    cutout, size = make_peek(listing, x, y)
    try:
        with transaction.atomic():
            peek = PhotoPeek.objects.create(
                listing=listing, customer_user_id=request.user.id, x=x, y=y, size=size, cutout=cutout,
            )
    except IntegrityError:
        # Two taps at once: the first one saved wins.
        return _already_peeked(PhotoPeek.objects.get(listing=listing, customer_user_id=request.user.id))

    return Response(peek_payload(peek), status=status.HTTP_201_CREATED)


def _already_peeked(peek):
    return _error(
        "already_peeked", "You've already had your one peek at this photo.", status.HTTP_409_CONFLICT,
        peek=peek_payload(peek),
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsCustomer])
def my_peeks(request):
    """The customer's saved peeks for the offers on one page."""
    raw = [part for part in request.query_params.get("listings", "").split(",") if part.strip()]
    if len(raw) > MAX_LISTINGS_PER_LOOKUP or not all(part.strip().isdigit() for part in raw):
        return _error(
            "invalid", f"listings is up to {MAX_LISTINGS_PER_LOOKUP} offer ids, separated by commas.",
            status.HTTP_400_BAD_REQUEST,
        )
    ids = {int(part) for part in raw}
    peeks = PhotoPeek.objects.filter(
        customer_user_id=request.user.id, listing_id__in=ids, listing__photo_effect=MAGIC,
    ).order_by("listing_id")
    return Response([peek_payload(peek) for peek in peeks])


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsShopOperator])
def owner_original_photo(request, pk):
    """The owner always sees their real photo, whatever customers get."""
    shop = Shop.objects.filter(operator_user_id=request.user.id).first()
    listing = Listing.objects.filter(pk=pk, shop_id=shop.id).first() if shop else None
    if listing is None or not listing.photo:
        raise Http404("Photo not found.")
    content_type = mimetypes.guess_type(listing.photo.name)[0] or "application/octet-stream"
    response = FileResponse(listing.photo.open("rb"), content_type=content_type)
    response["Cache-Control"] = "private, no-store"
    return response


def guarded_media(request, path):
    """Uploaded files for local development, except the original photo of a
    Blurred or Magic offer, which looks missing (so guessing gets nothing).

    Refused unless it is exactly one file in a known upload folder, so no
    spelling the file system treats as the same file ("..", "\\", "./",
    trailing dots, which Windows ignores) gets past the check. Originals are
    served only when a Normal offer uses them; letter case is ignored there,
    as Windows ignores it.
    """
    match = MEDIA_FILE.fullmatch(path)
    if match is None:
        raise Http404
    if match.group("folder").lower() == "listing_photos" and not Listing.objects.filter(
        photo__iexact=path, photo_effect=NORMAL,
    ).exists():
        raise Http404
    return serve(request, path, document_root=settings.MEDIA_ROOT)
