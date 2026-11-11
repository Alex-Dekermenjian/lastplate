"""LP-13: Which offers the public marketplace may show.

An offer is visible only while it is reservable (database-design.md §9):
- the listing is PUBLISHED (not a draft, not withdrawn),
- its shop is APPROVED,
- pickup has not ended yet, and
- it has free units, as calculated by stock.free_quantity().

Shop details come from one extra query for the whole page, never one query
per listing.
"""

from django.db.models import Count, Q
from django.utils import timezone

from .models import Listing, Shop
from .stock import free_quantity


def _approved_shops():
    return Shop.objects.filter(
        approval_status=Shop.ApprovalStatus.APPROVED
    )


def _open_listings(now):
    # LP-68: each offer's branch comes in the same query.
    return Listing.objects.select_related("branch").filter(
        status=Listing.Status.PUBLISHED,
        pickup_end__gt=now,
        shop_id__in=_approved_shops().values("id"),
    )


def _with_shops(listings):
    """Pair each listing with its shop, using one query for all shops.

    The shop is re-checked here, so a shop rejected between the two queries
    is still left out.
    """
    # LP-68: and how many branches each has, in the same query.
    shops = _approved_shops().annotate(
        active_branch_count=Count("branches", filter=Q(branches__removed_at__isnull=True)),
    ).in_bulk({listing.shop_id for listing in listings})

    return [
        (listing, shops[listing.shop_id])
        for listing in listings
        if listing.shop_id in shops
    ]


def visible_listings(now=None, shop_id=None):
    """Every offer customers can browse, with its shop, ending soonest first.

    LP-39: shop_id limits the result to one restaurant's offers.
    """
    now = now or timezone.now()

    open_listings = _open_listings(now)
    if shop_id is not None:
        open_listings = open_listings.filter(shop_id=shop_id)

    listings = [
        listing
        for listing in open_listings.order_by("pickup_end", "id")
        if free_quantity(listing) > 0
    ]

    return _with_shops(listings)


def visible_shop(pk):
    """LP-39: an approved restaurant customers may open, or None. Pending and
    rejected restaurants look the same as missing ones."""
    return _approved_shops().filter(pk=pk).first()


def visible_listing(pk, now=None):
    """One browsable offer with its shop, or None if customers cannot see it."""
    now = now or timezone.now()

    listing = _open_listings(now).filter(pk=pk).first()

    if listing is None or free_quantity(listing) <= 0:
        return None

    paired = _with_shops([listing])
    return paired[0] if paired else None
