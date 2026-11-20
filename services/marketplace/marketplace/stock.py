"""LP-12 and LP-20: Free-stock calculation for listings.

free = total_quantity - committed

"Committed" is stock held for (HELD) or sold to (CONSUMED) reservations,
from the Allocation table (docs/microservices-plan.md §3). Released,
restaurant-cancelled and rejected allocations do not count.

Pages that list many offers call with_committed() on their query, so the
count comes in the same query instead of one extra query per offer. Never use
it on a select_for_update() query: PostgreSQL refuses FOR UPDATE with SUM.
"""

from django.db.models import Q, Sum
from django.db.models.functions import Coalesce

COMMITTED_ANNOTATION = "committed_units"


def _committed_statuses():
    from .models import Allocation

    return list(Allocation.COMMITTED)


def with_committed(listings):
    """The listing query, with each listing's committed units counted in it."""
    return listings.annotate(**{
        COMMITTED_ANNOTATION: Coalesce(
            Sum("allocations__quantity", filter=Q(allocations__status__in=_committed_statuses())),
            0,
        )
    })


def committed_quantity(listing):
    """Units of this listing committed to reservations (HELD + CONSUMED)."""
    counted = getattr(listing, COMMITTED_ANNOTATION, None)
    if counted is not None:
        return counted
    if listing.pk is None:
        # Not saved yet, so nothing can be held for it.
        return 0

    from .models import Allocation

    return (
        Allocation.objects.filter(listing_id=listing.pk, status__in=_committed_statuses())
        .aggregate(total=Coalesce(Sum("quantity"), 0))["total"]
    )


HELD_ANNOTATION = "held_units"
SOLD_ANNOTATION = "sold_units"


def with_allocation_counts(listings):
    """LP-29: the owner's list also needs held (reserved) and sold units
    apart, counted in the same query."""
    from .models import Allocation

    def units(status):
        return Coalesce(Sum("allocations__quantity", filter=Q(allocations__status=status)), 0)

    return with_committed(listings).annotate(**{
        HELD_ANNOTATION: units(Allocation.Status.HELD),
        SOLD_ANNOTATION: units(Allocation.Status.CONSUMED),
    })


def _units(listing, annotation, status):
    counted = getattr(listing, annotation, None)
    if counted is not None:
        return counted
    if listing.pk is None:
        return 0

    from .models import Allocation

    return (
        Allocation.objects.filter(listing_id=listing.pk, status=status)
        .aggregate(total=Coalesce(Sum("quantity"), 0))["total"]
    )


def held_quantity(listing):
    """Units reserved by customers and not picked up yet (HELD)."""
    from .models import Allocation

    return _units(listing, HELD_ANNOTATION, Allocation.Status.HELD)


def sold_quantity(listing):
    """Units picked up and paid for (CONSUMED)."""
    from .models import Allocation

    return _units(listing, SOLD_ANNOTATION, Allocation.Status.CONSUMED)


def free_quantity(listing):
    """Units of this listing not committed to any reservation."""
    return listing.total_quantity - committed_quantity(listing)
