"""LP-37: A restaurant's request to join LastPlate, and image changes.

One owner account has at most one shop (Shop.operator_user_id is unique).
Sending the request creates that shop, or updates it while it is still
Pending. A shop that was already approved or rejected can't be re-sent.
"""

from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import Shop

IMAGE_FIELDS = ("photo", "logo")


class ApplicationClosed(Exception):
    """The shop was already reviewed, so its request can't be changed."""


def image_names(shop):
    return {
        field: getattr(shop, field).name
        for field in IMAGE_FIELDS
        if getattr(shop, field)
    }


def delete_replaced_images(shop, old_names):
    """Remove old image files only after the new ones are saved."""
    for field, old_name in old_names.items():
        image = getattr(shop, field)
        if old_name != image.name:
            image.storage.delete(old_name)


def submit_application(operator_user_id, data):
    """Save the owner's request as a Pending shop. Returns (shop, created).

    data holds validated application fields, without the conditions tick;
    the time it was accepted is recorded here.
    """
    for attempt in range(2):
        try:
            with transaction.atomic():
                shop = (
                    Shop.objects.select_for_update()
                    .filter(operator_user_id=operator_user_id)
                    .first()
                )
                created = shop is None

                if created:
                    shop = Shop(operator_user_id=operator_user_id)
                elif shop.approval_status != Shop.ApprovalStatus.PENDING:
                    raise ApplicationClosed

                old_images = {} if created else image_names(shop)

                for field, value in data.items():
                    setattr(shop, field, value)

                shop.approval_status = Shop.ApprovalStatus.PENDING
                shop.conditions_accepted_at = timezone.now()
                shop.save()
        except IntegrityError:
            # A second request created this owner's shop at the same moment.
            # Never make another one: update the shop that now exists.
            if attempt:
                raise
            continue

        delete_replaced_images(shop, old_images)
        return shop, created
