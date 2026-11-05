"""LP-28 and LP-19: hold and release stock for an order's lines.

An order's lines are held together: every listing is locked (in id order, so
two checkouts can't deadlock), every line is checked, and either all lines
are HELD or all are stored as REJECTED with the reason. Storing the outcome
makes a repeated request get the same answer. A release frees a line's units
only once (docs/microservices-plan.md section 6).
"""

from collections import defaultdict

from django.db import transaction
from django.utils import timezone

from .models import Allocation, Listing, Shop
from .stock import free_quantity

Status = Allocation.Status

# Stored on the lines that were fine when another line made the order fail.
OTHER_LINE_FAILED = "other_line_failed"


class AllocationConflict(Exception):
    """The request contradicts what is stored (for example another listing)."""


def _line_problem(listing, shop, wanted, now):
    if listing.status != Listing.Status.PUBLISHED:
        return "not_published"
    if shop is None or shop.approval_status != Shop.ApprovalStatus.APPROVED:
        return "shop_not_approved"
    if listing.pickup_end is None or listing.pickup_end <= now:
        return "pickup_ended"
    if free_quantity(listing) < wanted:
        return "not_enough_stock"
    return ""


def allocate_lines(lines, now=None):
    """lines: [{"reservation_ref", "listing_id", "quantity"}, ...] from one shop.

    Returns (allocations, created). All allocations are HELD, or all REJECTED.
    A repeat of the same request returns the stored allocations unchanged.
    Raises Listing.DoesNotExist or AllocationConflict.
    """
    now = now or timezone.now()
    refs = [line["reservation_ref"] for line in lines]
    if len(set(refs)) != len(refs):
        raise AllocationConflict("Each line needs its own reference.")

    with transaction.atomic():
        listing_ids = sorted({line["listing_id"] for line in lines})
        listings = {listing.pk: listing for listing in
                    Listing.objects.select_for_update().filter(pk__in=listing_ids).order_by("pk")}
        if len(listings) != len(listing_ids):
            raise Listing.DoesNotExist

        existing = {a.reservation_ref: a for a in Allocation.objects.filter(reservation_ref__in=refs)}
        if existing:
            if len(existing) != len(refs) or any(
                existing[line["reservation_ref"]].listing_id != line["listing_id"] for line in lines
            ):
                raise AllocationConflict("These references were used for a different order.")
            return [existing[ref] for ref in refs], False

        if len({listing.shop_id for listing in listings.values()}) != 1:
            raise AllocationConflict("All lines must come from one restaurant.")
        shop = Shop.objects.filter(pk=next(iter(listings.values())).shop_id).first()

        # The same listing on two lines counts once, with the summed quantity.
        wanted = defaultdict(int)
        for line in lines:
            wanted[line["listing_id"]] += line["quantity"]
        problems = {pk: _line_problem(listings[pk], shop, qty, now) for pk, qty in wanted.items()}
        failed = any(problems.values())

        allocations = [
            Allocation.objects.create(
                reservation_ref=line["reservation_ref"],
                listing=listings[line["listing_id"]],
                quantity=line["quantity"],
                status=Status.REJECTED if failed else Status.HELD,
                reject_reason=(problems[line["listing_id"]] or OTHER_LINE_FAILED) if failed else "",
            )
            for line in lines
        ]
        return allocations, True


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
