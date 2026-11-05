"""LP-12: Free-stock calculation for listings.

free = total_quantity - committed

"Committed" is stock held for or collected by orders: the sum of HELD and
CONSUMED allocation quantities (docs/microservices-plan.md §3, LP-28).
"""

from django.db.models import Q, Sum
from django.db.models.functions import Coalesce

COMMITTED_STATUSES = ("HELD", "CONSUMED")


def with_committed(listings):
    """Annotate a Listing queryset with committed_units, so pages that show
    many offers calculate free stock without one query per offer."""
    return listings.annotate(
        committed_units=Coalesce(
            Sum("allocations__quantity", filter=Q(allocations__status__in=COMMITTED_STATUSES)), 0
        )
    )


def committed_quantity(listing):
    """Units of this listing committed to reservations."""
    from .models import Allocation

    annotated = getattr(listing, "committed_units", None)
    if annotated is not None:
        return annotated
    if listing.pk is None:
        return 0
    return (
        Allocation.objects.filter(listing_id=listing.pk, status__in=Allocation.COUNTS_AGAINST_STOCK)
        .aggregate(total=Sum("quantity"))["total"]
        or 0
    )


def free_quantity(listing):
    """Units of this listing not committed to any reservation."""
    return listing.total_quantity - committed_quantity(listing)
