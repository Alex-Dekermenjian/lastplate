"""LP-58: quick actions from the owner's offer list.

- Mark sold out: the free units go to 0, written in the stock history like
  any other stock change (LP-12), so the offer leaves the public pages.
- Delete draft: only drafts. A published offer is never deleted; it ends
  or sells out instead, so orders and history always keep their offer.
"""

from django.db import transaction

from .models import Listing
from .stock import free_quantity
from .stock_service import StockAdjustmentError, adjust_stock

SOLD_OUT_REASON = "Marked sold out"


def mark_sold_out(listing_id, shop_id, actor_user_id):
    """Take every free unit off sale. Returns (listing, adjustment)."""
    with transaction.atomic():
        listing = Listing.objects.select_for_update().filter(pk=listing_id, shop_id=shop_id).first()
        if listing is None:
            raise StockAdjustmentError("not_owner", "You do not own this listing.", http_status=403)
        if listing.status != Listing.Status.PUBLISHED:
            raise StockAdjustmentError(
                "not_published", "Only published offers can be marked sold out.", http_status=409,
            )
        free = free_quantity(listing)
        if free <= 0:
            raise StockAdjustmentError("already_sold_out", "This offer is already sold out.", http_status=409)
        # adjust_stock locks the same row again (fine inside one transaction)
        # and checks the rest: pickup not ended, never below what customers
        # already reserved.
        return adjust_stock(
            listing_id=listing_id,
            shop_id=shop_id,
            quantity_change=-free,
            reason=SOLD_OUT_REASON,
            actor_user_id=actor_user_id,
        )


class DraftDeleteError(Exception):
    def __init__(self, code, detail, http_status):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.http_status = http_status


def delete_draft(listing_id, shop_id):
    with transaction.atomic():
        listing = Listing.objects.select_for_update().filter(pk=listing_id, shop_id=shop_id).first()
        if listing is None:
            raise DraftDeleteError("not_found", "Draft listing not found.", 404)
        if listing.status != Listing.Status.DRAFT:
            raise DraftDeleteError(
                "not_a_draft",
                "Only drafts can be deleted. A published offer ends or sells out instead.",
                409,
            )
        photo = listing.photo.name if listing.photo else ""
        storage = listing.photo.storage if listing.photo else None
        listing.delete()
    # The photo file goes only after the row is gone for good.
    if photo and storage is not None:
        storage.delete(photo)
