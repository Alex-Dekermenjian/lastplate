
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


