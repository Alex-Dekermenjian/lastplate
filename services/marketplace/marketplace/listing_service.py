
from django.db import transaction

from .models import Listing
from .serializers import ListingSerializer
from django.core.exceptions import ValidationError

from .validators import (
    required_for_publication,
    validate_listing_data,
)
from django.core.exceptions import PermissionDenied


def save_listing_draft(data, shop_id, shop=None):
    """
    Save a private draft belonging to a shop.

    The caller must authenticate the operator and verify
    shop ownership before calling this function. The shop, when given,
    names a LastBag that has no title (LP-38).
    """

    serializer = ListingSerializer(data=data, context={"shop": shop})
    serializer.is_valid(raise_exception=True)

    with transaction.atomic():
        listing = serializer.save(
            shop_id=shop_id,
            status=Listing.Status.DRAFT,
        )

    return listing

def validate_listing_for_publication(listing):
    """
    Check that a listing contains all information
    required before publication.
    """

    # LP-38: A LastBag and a specific item need different details.
    errors = required_for_publication(
        listing.kind,
        lambda field: getattr(listing, field, ""),
    )

    try:
        validate_listing_data(
            original_price=listing.original_price,
            discounted_price=listing.discounted_price,
            stock=listing.total_quantity,
            pickup_start=listing.pickup_start,
            pickup_end=listing.pickup_end,
            publishing=True,
            kind=listing.kind,
        )
    except ValidationError as exc:
        errors.update(exc.message_dict)

    if errors:
        raise ValidationError(errors)

def publish_listing(listing_id, shop_id, shop_approved=False, profile_complete=False):
    """
    Publish an existing draft only after checking
    ownership, shop approval, profile and listing data.

    shop_id, shop_approved and profile_complete must
    come from trusted server-side checks, not user JSON.
    """

    with transaction.atomic():
        listing = Listing.objects.select_for_update().get(
            pk=listing_id
        )

        if listing.shop_id != shop_id:
            raise PermissionDenied(
                "You do not own this food listing."
            )

        if not shop_approved:
            raise PermissionDenied(
                "Your shop must be approved before publishing."
            )

        if not profile_complete:
            raise PermissionDenied(
                "Complete your shop profile before publishing."
            )

        if listing.status != Listing.Status.DRAFT:
            raise ValidationError(
                "Only draft listings can be published."
            )

        validate_listing_for_publication(listing)

        listing.status = Listing.Status.PUBLISHED
        listing.save(update_fields=["status", "updated_at"])

    return listing


# --------------------------------------------------
# LP-29: Take a published offer down (unpublish)
# --------------------------------------------------

class UnpublishError(Exception):
    def __init__(self, code, detail, http_status):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.http_status = http_status


def withdraw_listing(listing_id, shop_id):
    """PUBLISHED -> WITHDRAWN. Customers no longer see it and every new
    reserve call is refused (not_published), but existing allocations are not
    touched: held units stay held, so those orders are still valid and can be
    picked up and paid (consume works on a withdrawn offer). A withdrawn offer
    stays withdrawn; the restaurant posts a new one instead.

    The listing lock is the same one reserve calls take, so a reserve that
    arrives after this commits is refused, and one that got in first is held.
    """
    with transaction.atomic():
        listing = Listing.objects.select_for_update().filter(pk=listing_id, shop_id=shop_id).first()
        if listing is None:
            raise UnpublishError("not_owner", "You do not own this listing.", 403)
        if listing.status == Listing.Status.WITHDRAWN:
            # Pressed twice: already done.
            return listing
        if listing.status != Listing.Status.PUBLISHED:
            raise UnpublishError("not_published", "Only published offers can be taken down.", 409)
        listing.status = Listing.Status.WITHDRAWN
        listing.save(update_fields=["status", "updated_at"])
    return listing
