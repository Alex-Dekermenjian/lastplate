"""LP-28 (draft for LP-19): hold and release stock for order lines.

Every call is idempotent on reservation_ref and runs under the listing's row
lock, so concurrent orders cannot oversell and a repeated release frees the
units only once (docs/microservices-plan.md section 6).
"""

from django.db import transaction
from django.utils import timezone

from .models import Allocation, Listing, Shop
from .stock import free_quantity

Status = Allocation.Status


class AllocationConflict(Exception):
    """The request contradicts the stored allocation (for example another listing)."""


def allocate(reservation_ref, listing_id, quantity, now=None):
    """Return (allocation, created). A new allocation is HELD when the listing
    is open, its shop approved and enough is free; otherwise it is stored as
    REJECTED with a reason, so a retry always gets the same answer."""
    now = now or timezone.now()
    with transaction.atomic():
        listing = Listing.objects.select_for_update().filter(pk=listing_id).first()
        if listing is None:
            raise Listing.DoesNotExist
        existing = Allocation.objects.filter(reservation_ref=reservation_ref).first()
        if existing is not None:
            if existing.listing_id != listing.pk:
                raise AllocationConflict("This reference belongs to another listing.")
            return existing, False

        reason = ""
        shop = Shop.objects.filter(pk=listing.shop_id).first()
        if listing.status != Listing.Status.PUBLISHED:
            reason = "not_published"
        elif shop is None or shop.approval_status != Shop.ApprovalStatus.APPROVED:
            reason = "shop_not_approved"
        elif listing.pickup_end is None or listing.pickup_end <= now:
            reason = "pickup_ended"
        elif free_quantity(listing) < quantity:
            reason = "not_enough_stock"

        allocation = Allocation.objects.create(
            reservation_ref=reservation_ref,
            listing=listing,
            quantity=quantity,
            status=Status.REJECTED if reason else Status.HELD,
            reject_reason=reason,
        )
        return allocation, True


def release(reservation_ref, listing_id):
    """HELD → RELEASED (frees the units once). Already RELEASED: no change.
    Unknown reference: a RELEASED tombstone with quantity 0, so a late
    allocate for it can never hold units. CONSUMED or SHOP_CANCELLED cannot
    be released and raise AllocationConflict."""
    with transaction.atomic():
        listing = Listing.objects.select_for_update().filter(pk=listing_id).first()
        if listing is None:
            raise Listing.DoesNotExist
        allocation = Allocation.objects.filter(reservation_ref=reservation_ref).first()
        if allocation is None:
            return Allocation.objects.create(
                reservation_ref=reservation_ref, listing=listing, quantity=0, status=Status.RELEASED,
            )
        if allocation.listing_id != listing.pk:
            raise AllocationConflict("This reference belongs to another listing.")
        if allocation.status == Status.HELD:
            allocation.status = Status.RELEASED
            allocation.save(update_fields=["status", "updated_at"])
        elif allocation.status not in (Status.RELEASED, Status.REJECTED):
            raise AllocationConflict(f"A {allocation.status.lower()} allocation cannot be released.")
        return allocation
