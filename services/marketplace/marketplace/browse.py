"""LP-13: Which offers the public marketplace may show.

An offer is visible only while it is reservable (database-design.md §9):
- the listing is PUBLISHED (not a draft, not withdrawn),
- its shop is APPROVED,
- pickup has not ended yet, and
- it has free units, as calculated by stock.free_quantity().

Shop details come from one extra query for the whole page, never one query
per listing.
"""

from django.utils import timezone

from .models import Listing, Shop
from .stock import free_quantity, with_committed


def _approved_shops():
    return Shop.objects.filter(
        approval_status=Shop.ApprovalStatus.APPROVED
    )


def _open_listings(now):
    return with_committed(Listing.objects).filter(
        status=Listing.Status.PUBLISHED,
        pickup_end__gt=now,
        shop_id__in=_approved_shops().values("id"),
    )


def _with_shops(listings):
    """Pair each listing with its shop, using one query for all shops.

    The shop is re-checked here, so a shop rejected between the two queries
    is still left out.
    """
    shops = _approved_shops().in_bulk({listing.shop_id for listing in listings})

    return [
        (listing, shops[listing.shop_id])
        for listing in listings
        if listing.shop_id in shops
    ]


def visible_listings(now=None):
    """Every offer customers can browse, with its shop, ending soonest first."""
    now = now or timezone.now()

    listings = [
        listing
        for listing in _open_listings(now).order_by("pickup_end", "id")
        if free_quantity(listing) > 0
    ]

    return _with_shops(listings)


def visible_listing(pk, now=None):
    """One browsable offer with its shop, or None if customers cannot see it."""
    now = now or timezone.now()

    listing = _open_listings(now).filter(pk=pk).first()

    if listing is None or free_quantity(listing) <= 0:
        return None

    paired = _with_shops([listing])
    return paired[0] if paired else None
