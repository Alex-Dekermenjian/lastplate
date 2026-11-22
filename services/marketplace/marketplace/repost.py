"""LP-44: repost an ended offer as today's draft.

The owner presses Repost on an offer in the Ended tab and gets a NEW draft
with the same food, prices, branch and photo, and the same pickup hours but
on today's date. Quantity is left empty (0, which can't be published), so the
owner enters how many they have today and publishes as usual.

The old offer is only read, never changed: its orders, stock history and
sales keep pointing at it exactly as they were.
"""

from datetime import datetime, timedelta

from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from .models import Listing

# What the new draft copies from the old offer. Stock, status, terms, the
# reservation history and LP-60 removal details are never copied.
COPIED_FIELDS = (
    "shop_id",
    "branch_id",
    "kind",
    "title",
    "description",
    "category",
    "typical_contents",
    "ingredients",
    "allergens",
    "original_price",
    "discounted_price",
    "max_per_customer",
    "pickup_address",
)


class RepostError(Exception):
    def __init__(self, code, detail, http_status):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.http_status = http_status


def has_ended(listing, now):
    """Ended the way the owner's Ended tab shows it: taken down (by the owner
    or by LastPlate), or published and its pickup time is over."""
    if listing.status == Listing.Status.WITHDRAWN:
        return True
    return (
        listing.status == Listing.Status.PUBLISHED
        and listing.pickup_end is not None
        and listing.pickup_end <= now
    )


def same_hours_today(pickup_start, pickup_end, now):
    """The same local clock hours on today's date, or tomorrow's when today's
    pickup end has already passed. A window that ran past midnight still
    does (same number of days between start and end)."""
    if pickup_start is None or pickup_end is None:
        return None, None
    start = timezone.localtime(pickup_start)
    end = timezone.localtime(pickup_end)
    days_apart = (end.date() - start.date()).days
    day = timezone.localdate(now)

    def window(on):
        return (
            timezone.make_aware(datetime.combine(on, start.time().replace(tzinfo=None))),
            timezone.make_aware(datetime.combine(on + timedelta(days=days_apart), end.time().replace(tzinfo=None))),
        )

    new_start, new_end = window(day)
    if new_end <= now:
        new_start, new_end = window(day + timedelta(days=1))
    return new_start, new_end


def copy_photo(photo):
    """A separate file with the same picture, so deleting either offer's
    photo (or the draft) never removes the other's. None when the old file
    is gone."""
    if not photo:
        return None
    try:
        with photo.storage.open(photo.name, "rb") as source:
            content = source.read()
    except OSError:
        return None
    return ContentFile(content, name=photo.name.rsplit("/", 1)[-1])


def repost_listing(listing_id, shop_id):
    """Create today's draft from one of this shop's ended offers."""
    now = timezone.now()
    with transaction.atomic():
        old = Listing.objects.select_related("branch").filter(pk=listing_id, shop_id=shop_id).first()
        if old is None:
            # Another shop's offer looks the same as no offer at all.
            raise RepostError("not_owner", "You can only repost your own offers.", 403)
        if not has_ended(old, now):
            raise RepostError("not_ended", "Only ended offers can be reposted.", 409)
        if old.branch is not None and not old.branch.is_active:
            raise RepostError(
                "branch_removed",
                "This offer's branch was removed, so it can't be reposted there. Create a new offer instead.",
                409,
            )

        draft = Listing(**{field: getattr(old, field) for field in COPIED_FIELDS})
        draft.status = Listing.Status.DRAFT
        draft.total_quantity = 0  # the owner enters today's quantity
        draft.pickup_start, draft.pickup_end = same_hours_today(old.pickup_start, old.pickup_end, now)

        photo = copy_photo(old.photo)
        if photo is not None:
            # upload_to gives the copy its own new random name.
            draft.photo.save(photo.name, photo, save=False)
        try:
            draft.save()
        except Exception:
            if draft.photo:
                draft.photo.storage.delete(draft.photo.name)
            raise
    return draft
