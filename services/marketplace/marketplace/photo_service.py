
from django.core.exceptions import ValidationError
from django.db import transaction

from .image_validators import validate_listing_photo
from .models import Listing
from .photo_effects import sync_blurred_photo


def replace_listing_photo(listing_id, shop_id, uploaded_file):
    """
    Replace a listing photo after checking ownership
    and validating the uploaded image.
    """

    # Validate before changing anything.
    validate_listing_photo(uploaded_file)

    with transaction.atomic():
        listing = Listing.objects.select_for_update().get(
            pk=listing_id,
            shop_id=shop_id,
        )

        # Save the new photo first.
        old_photo_name = listing.photo.name if listing.photo else None

        listing.photo = uploaded_file
        listing.save(update_fields=["photo", "updated_at"])

    # LP-54: a Blurred or Magic offer gets a new blurred copy of the new photo.
    sync_blurred_photo(listing, photo_changed=True)

    # Remove the old photo only after the new one is saved.
    if old_photo_name and old_photo_name != listing.photo.name:
        listing.photo.storage.delete(old_photo_name)

    return listing
