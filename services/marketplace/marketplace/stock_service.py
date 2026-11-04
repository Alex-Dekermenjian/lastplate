"""LP-12: Manual stock adjustments by shop operators."""

from django.db import transaction
from django.utils import timezone

from .models import Listing, StockAdjustment
from .stock import committed_quantity

# Largest value Listing.total_quantity (PositiveIntegerField) can hold.
MAX_TOTAL_QUANTITY = 2147483647


class StockAdjustmentError(Exception):
    """An adjustment was refused. code and detail go into the API error."""

    def __init__(self, code, detail, http_status=400):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.http_status = http_status


def adjust_stock(listing_id, shop_id, quantity_change, reason, actor_user_id):
    """
    Change a published listing's total stock and record why.

    shop_id and actor_user_id must come from the verified token, never
    from the request. The listing is locked, every rule is rechecked
    after locking, and the stock update and its StockAdjustment are
    saved in one transaction. terms_revision is not changed.
    """

    reason = (reason or "").strip()

    if quantity_change == 0:
        raise StockAdjustmentError(
            "zero_change",
            "Enter a quantity change other than zero.",
        )

    if not reason:
        raise StockAdjustmentError(
            "reason_required",
            "Give a reason for the stock change.",
        )

    with transaction.atomic():
        listing = (
            Listing.objects.select_for_update()
            .filter(pk=listing_id)
            .first()
        )

        if listing is None or listing.shop_id != shop_id:
            raise StockAdjustmentError(
                "not_owner",
                "You do not own this listing.",
                http_status=403,
            )

        if listing.status != Listing.Status.PUBLISHED:
            raise StockAdjustmentError(
                "not_published",
                "Only published offers can have their stock adjusted.",
                http_status=409,
            )

        # Server time, checked after locking.
        if listing.pickup_end is None or listing.pickup_end <= timezone.now():
            raise StockAdjustmentError(
                "pickup_ended",
                "The pickup window has ended, so stock can no longer change.",
                http_status=409,
            )

        new_total = listing.total_quantity + quantity_change

        if new_total < 0:
            raise StockAdjustmentError(
                "below_zero",
                "Stock cannot go below zero.",
            )

        committed = committed_quantity(listing)

        if new_total < committed:
            raise StockAdjustmentError(
                "below_committed",
                f"Stock cannot go below the {committed} units already "
                "reserved or collected.",
            )

        if new_total > MAX_TOTAL_QUANTITY:
            raise StockAdjustmentError(
                "too_large",
                "That quantity is too large.",
            )

        listing.total_quantity = new_total
        listing.save(update_fields=["total_quantity", "updated_at"])

        adjustment = StockAdjustment.objects.create(
            listing=listing,
            quantity_change=quantity_change,
            reason=reason,
            kind=StockAdjustment.Kind.MANUAL,
            actor_user_id=actor_user_id,
        )

    return listing, adjustment
