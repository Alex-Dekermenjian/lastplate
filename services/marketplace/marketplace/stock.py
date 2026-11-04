"""LP-12: Free-stock calculation for listings.

free = total_quantity - committed

"Committed" is stock held for or collected by reservations. Allocation
arrives in Sprint 2; until then nothing is committed. When it does, only
committed_quantity() needs to change, to the sum of HELD and CONSUMED
allocation quantities (docs/microservices-plan.md §3).
"""


def committed_quantity(listing):
    """Units of this listing committed to reservations."""
    return 0


def free_quantity(listing):
    """Units of this listing not committed to any reservation."""
    return listing.total_quantity - committed_quantity(listing)
