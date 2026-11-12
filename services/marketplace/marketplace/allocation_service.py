"""LP-20: holding, returning and selling stock for reservations.

Marketplace is the only service that changes stock. Reservations asks for
it through the internal API (internal_views.py); these functions do the work.

Rules (docs/microservices-plan.md §6):
- Every call first locks the listing row (select_for_update), so calls for
  the same offer run one after another and stock can never be oversold.
- reservation_ref is unique: the same call sent twice finds the same
  Allocation and changes stock only once.
- Only HELD moves. CONSUMED, RELEASED, SHOP_CANCELLED and REJECTED are final.
- A refused reserve call is stored as REJECTED with a reason, so a retry
  always gets the same answer.
"""

from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import Allocation, Listing, Shop, StockAdjustment
from .stock import free_quantity


class AllocationError(Exception):
    """A call that changes nothing. code and detail go into the API error."""

    def __init__(self, code, detail, http_status):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.http_status = http_status


def _locked_listing(listing_id):
    listing = Listing.objects.select_for_update().filter(pk=listing_id).first()
    # Drafts are never shown to customers, so for this API they do not exist.
    if listing is None or listing.status == Listing.Status.DRAFT:
        raise AllocationError("listing_not_found", "No such offer.", 404)
    return listing


def _ref_conflict():
    return AllocationError(
        "ref_conflict",
        "This reservation reference is already used for a different request.",
        409,
    )


def _check_same_request(allocation, listing_id, quantity=None):
    """The same reservation_ref must always mean the same listing and quantity."""
    if allocation.listing_id != listing_id:
        raise _ref_conflict()
    # A release tombstone (quantity 0) is returned to any later reserve call.
    if quantity is not None and allocation.quantity not in (quantity, 0):
        raise _ref_conflict()


def _reject_reason(listing, quantity, expected_terms_revision, now):
    if listing.status != Listing.Status.PUBLISHED:
        return Allocation.RejectReason.NOT_PUBLISHED
    shop_approved = Shop.objects.filter(
        pk=listing.shop_id, approval_status=Shop.ApprovalStatus.APPROVED,
    ).exists()
    if not shop_approved:
        return Allocation.RejectReason.SHOP_NOT_APPROVED
    if listing.pickup_end is None or listing.pickup_end <= now:
        return Allocation.RejectReason.PICKUP_ENDED
    if listing.terms_revision != expected_terms_revision:
        return Allocation.RejectReason.TERMS_CHANGED
    if free_quantity(listing) < quantity:
        return Allocation.RejectReason.NOT_ENOUGH_STOCK
    return ""


def _snapshot(listing):
    return {
        "title": listing.title,
        "unit_price": listing.discounted_price,
        "pickup_start": listing.pickup_start,
        "pickup_end": listing.pickup_end,
        "terms_revision": listing.terms_revision,
        "shop_id": listing.shop_id,
        "branch_id": listing.branch_id,
    }


def _existing(reservation_ref, lock=False):
    allocations = Allocation.objects.filter(reservation_ref=reservation_ref)
    if lock:
        allocations = allocations.select_for_update()
    return allocations.first()


def reserve(reservation_ref, listing_id, quantity, expected_terms_revision, now=None):
    """Hold `quantity` units for one reservation, or store why not.

    Returns (allocation, created). allocation.status is HELD or REJECTED for
    a new call; a repeated call returns the stored allocation unchanged.
    """
    with transaction.atomic():
        listing = _locked_listing(listing_id)

        existing = _existing(reservation_ref)
        if existing is not None:
            _check_same_request(existing, listing_id, quantity)
            return existing, False

        # Server time, read after the lock is taken.
        moment = now or timezone.now()
        reason = _reject_reason(listing, quantity, expected_terms_revision, moment)
        try:
            with transaction.atomic():
                allocation = Allocation.objects.create(
                    reservation_ref=reservation_ref,
                    listing=listing,
                    quantity=quantity,
                    status=Allocation.Status.REJECTED if reason else Allocation.Status.HELD,
                    reject_reason=reason,
                    **_snapshot(listing),
                )
        except IntegrityError:
            # The same ref arrived at the same moment for another listing
            # (a different lock). Answer from the row that won.
            existing = _existing(reservation_ref)
            if existing is None:
                raise
            _check_same_request(existing, listing_id, quantity)
            return existing, False

        if not reason and listing.first_reserved_at is None:
            listing.first_reserved_at = moment
            listing.save(update_fields=["first_reserved_at", "updated_at"])

    return allocation, True


def _finish(allocation, to_status, now):
    allocation.status = to_status
    allocation.finished_at = now or timezone.now()
    allocation.save(update_fields=["status", "finished_at"])


def release(reservation_ref, listing_id, now=None):
    """Put held units back (customer cancellation or expiry).

    Returns (allocation, changed). Allowed after the pickup window ends too:
    the units then count as unsold, because an ended offer is no longer
    listed. A release that arrives before any reserve call stores a RELEASED
    "tombstone" with quantity 0, so a late reserve call cannot take stock.
    """
    with transaction.atomic():
        listing = _locked_listing(listing_id)
        allocation = _existing(reservation_ref, lock=True)

        if allocation is None:
            try:
                with transaction.atomic():
                    allocation = Allocation.objects.create(
                        reservation_ref=reservation_ref,
                        listing=listing,
                        quantity=0,
                        status=Allocation.Status.RELEASED,
                        finished_at=now or timezone.now(),
                        shop_id=listing.shop_id,
                        branch_id=listing.branch_id,
                    )
            except IntegrityError:
                allocation = _existing(reservation_ref)
                if allocation is None:
                    raise
                _check_same_request(allocation, listing_id)
                return allocation, False
            return allocation, True

        _check_same_request(allocation, listing_id)
        if allocation.status == Allocation.Status.HELD:
            _finish(allocation, Allocation.Status.RELEASED, now)
            return allocation, True
        if allocation.status in (Allocation.Status.RELEASED, Allocation.Status.REJECTED):
            # Already released, or nothing was ever held: nothing to do.
            return allocation, False
        raise AllocationError(
            "not_held",
            f"These units are {allocation.get_status_display().lower()} and cannot be released.",
            409,
        )


def consume(reservation_ref, listing_id, now=None):
    """Make the hold final: the customer picked up and paid.

    Returns (allocation, changed). Sold units stay counted against stock.
    """
    with transaction.atomic():
        _locked_listing(listing_id)
        allocation = _existing(reservation_ref, lock=True)
        if allocation is None:
            raise AllocationError("allocation_not_found", "No units are held for this reservation.", 404)

        _check_same_request(allocation, listing_id)
        if allocation.status == Allocation.Status.HELD:
            _finish(allocation, Allocation.Status.CONSUMED, now)
            return allocation, True
        if allocation.status == Allocation.Status.CONSUMED:
            return allocation, False
        raise AllocationError(
            "not_held",
            f"These units are {allocation.get_status_display().lower()} and cannot be sold.",
            409,
        )


def shop_cancel(reservation_ref, listing_id, actor_user_id, reason, now=None):
    """The restaurant cancels because the food is not available.

    The held units leave the offer's total stock instead of going back on
    sale: total goes down by the same amount and free stock stays the same
    (database-design.md §9). Writes one SHOP_CANCELLATION StockAdjustment,
    unique per reservation, so a retry never adjusts twice.
    Returns (allocation, changed).
    """
    reason = (reason or "").strip()
    if not reason:
        raise AllocationError("reason_required", "Give a reason for the cancellation.", 400)

    with transaction.atomic():
        listing = _locked_listing(listing_id)
        allocation = _existing(reservation_ref, lock=True)
        if allocation is None:
            raise AllocationError("allocation_not_found", "No units are held for this reservation.", 404)

        _check_same_request(allocation, listing_id)
        # The user ID travels in the body; check it against our own data.
        if not Shop.objects.filter(pk=listing.shop_id, operator_user_id=actor_user_id).exists():
            raise AllocationError("not_owner", "Only this offer's restaurant can cancel its reservations.", 403)

        if allocation.status == Allocation.Status.SHOP_CANCELLED:
            return allocation, False
        if allocation.status != Allocation.Status.HELD:
            raise AllocationError(
                "not_held",
                f"These units are {allocation.get_status_display().lower()} and cannot be cancelled.",
                409,
            )

        _finish(allocation, Allocation.Status.SHOP_CANCELLED, now)
        listing.total_quantity -= allocation.quantity
        listing.save(update_fields=["total_quantity", "updated_at"])
        StockAdjustment.objects.create(
            listing=listing,
            quantity_change=-allocation.quantity,
            reason=reason,
            kind=StockAdjustment.Kind.SHOP_CANCELLATION,
            reservation_ref=str(reservation_ref),
            actor_user_id=actor_user_id,
        )
    return allocation, True
