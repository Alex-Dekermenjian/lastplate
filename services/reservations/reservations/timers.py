"""LP-25: the timer job. Late orders expire and the food goes back on sale.

run_timers() does four things, each safe to run twice, at the same moment
from two processes, or late (the next run catches up):

1. expire_due_orders(): every Reserved (CONFIRMED) order whose pick_up_by has
   passed becomes EXPIRED (actor = system), with sync_state RELEASE_PENDING,
   under the order's row lock (reservations.orders.change_status). After that
   commits, each item's allocation is released in Marketplace. If the offer
   is still open, the units are on sale again at once (2 left -> 3). If the
   deadline was the offer's closing time, Marketplace no longer lists the
   offer, so nothing visibly goes back: the units count as unsold.
2. consume_paid_orders(): Paid orders older than LP-24's ten-minute undo
   window are claimed (collection.claim_consumption, a permanent undo
   barrier committed before any HTTP) and then consumed in Marketplace.
3. resolve_stuck_orders(): orders stuck in PROCESSING for more than two
   minutes (the customer never retried after "try again") become
   NOT_RESERVED, which is never shown, and every item is released. Release
   is idempotent and leaves a tombstone, so a late allocate call can never
   hold stock for them again (plan §6, "Cancellation versus a delayed
   allocate").
4. retry_pending_releases(): orders left in RELEASE_PENDING by a release
   that got no answer (customer cancel, expiry, checkout give-up) are
   released again until Marketplace confirms.

Marketplace's release and consume move an allocation only from HELD, so
however often this runs, the food is returned (or sold) once.

Expiry never depends only on the timer: expire_due_orders() also runs at the
start of every order read (customer orders, Today's orders, pickup lookup,
Paid, checkout) and, through POST /api/timers/expire, whenever the web shows
the offers list. The management command `run_timers` is the backup.
"""

import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from . import marketplace_client as market
from .checkout import item_ref
from .collection import UNDO_WINDOW, claim_consumption
from .models import Actor, Order, Status, SyncState
from .orders import InvalidStatusChange, change_status

logger = logging.getLogger("reservations.timers")

# An order still PROCESSING this long after it was placed is cleaned up.
STUCK_AFTER = timedelta(minutes=2)
# At most this many orders per step and run, so a page read stays quick.
BATCH = 50


def _ids(queryset, limit):
    return list(queryset.order_by("id").values_list("pk", flat=True)[:limit])


def _settle(order_pk, pending, results):
    """After one Marketplace call per item: SYNCED when every call was done;
    NEEDS_ATTENTION after an impossible answer; otherwise stay `pending` for
    the next run. Conditional, so it never overwrites a state set meanwhile."""
    if all(result == market.DONE for result in results):
        new_state = SyncState.SYNCED
    elif market.FAULT in results:
        new_state = SyncState.NEEDS_ATTENTION
    else:
        return False
    return Order.objects.filter(pk=order_pk, sync_state=pending).update(sync_state=new_state) == 1


def release_items(order):
    """Release every item of an order in RELEASE_PENDING. True once synced."""
    results = [market.stock_call("release", item_ref(order, line.listing_id), line.listing_id)
               for line in order.lines.all()]
    return _settle(order.pk, SyncState.RELEASE_PENDING, results)


def _move(pk, to_status, expected, reason, now):
    """One locked compare-and-set. None when another process got there first."""
    try:
        return change_status(Order(pk=pk), to_status, actor=Actor.SYSTEM, reason=reason,
                             sync_state=SyncState.RELEASE_PENDING, expected_status=expected, now=now)
    except InvalidStatusChange:
        return None


def expire_due_orders(now=None, limit=BATCH):
    """Reserved orders past their pick_up_by become Expired; their food is
    released. Returns the orders this call expired."""
    now = now or timezone.now()
    due = Order.objects.filter(status=Status.CONFIRMED, pick_up_by__lte=now)
    expired = [order for order in (
        _move(pk, Status.EXPIRED, Status.CONFIRMED, "Not picked up by the pick-up time", now)
        for pk in _ids(due, limit)) if order is not None]
    for order in expired:
        release_items(order)
    return expired


def expire_due_orders_quietly():
    """For order reads: expiry must never make a page fail."""
    try:
        return len(expire_due_orders())
    except Exception:  # noqa: BLE001 - logged; the timer will try again
        logger.exception("Expiring late orders failed; the timer job will retry.")
        return 0


def consume_order(pk, now=None):
    """Claim one Paid order, then consume every item. True once synced.
    (LP-43: the admin's Retry uses this for one order.)"""
    order = claim_consumption(pk, now=now)
    if order is None:
        return False  # undone, or not old enough, since the list was read
    results = [market.stock_call("consume", item_ref(order, line.listing_id), line.listing_id)
               for line in order.lines.all()]
    if all(result == market.DONE for result in results):
        with transaction.atomic():
            locked = Order.objects.select_for_update().get(pk=pk)
            # Only if the same Paid and the same claim still apply (LP-24 contract).
            if locked.status == Status.COLLECTED and locked.sync_state == SyncState.CONSUME_PENDING \
                    and locked.collection_version == order.collection_version \
                    and locked.consumption_started_at is not None:
                locked.sync_state = SyncState.SYNCED
                locked.save(update_fields=["sync_state"])
                return True
    elif market.FAULT in results:
        # The claim stays: never undo, never release a sold item.
        Order.objects.filter(pk=pk, sync_state=SyncState.CONSUME_PENDING).update(
            sync_state=SyncState.NEEDS_ATTENTION)
    return False


def consume_paid_orders(now=None, limit=BATCH):
    """Paid orders past the undo window: claim, then consume every item."""
    now = now or timezone.now()
    paid = Order.objects.filter(status=Status.COLLECTED, sync_state=SyncState.CONSUME_PENDING,
                                collected_at__lte=now - UNDO_WINDOW)
    return sum(1 for pk in _ids(paid, limit) if consume_order(pk, now))


def resolve_stuck_orders(now=None, limit=BATCH):
    """PROCESSING orders older than STUCK_AFTER become NOT_RESERVED (never
    shown) and anything they hold is released."""
    now = now or timezone.now()
    stuck = Order.objects.filter(status=Status.PROCESSING, sync_state=SyncState.ALLOCATE_PENDING,
                                 created_at__lte=now - STUCK_AFTER)
    resolved = [order for order in (
        _move(pk, Status.NOT_RESERVED, Status.PROCESSING, "Not confirmed in time; nothing kept", now)
        for pk in _ids(stuck, limit)) if order is not None]
    for order in resolved:
        release_items(order)
    return resolved


def retry_pending_releases(limit=BATCH):
    """Releases that got no answer earlier, whichever action started them."""
    pending = Order.objects.filter(sync_state=SyncState.RELEASE_PENDING).prefetch_related("lines")
    return sum(1 for order in pending.order_by("id")[:limit] if release_items(order))


def run_timers(now=None):
    """One run of every step. A failing step is logged and never stops the
    others. Returns how many orders each step handled."""
    now = now or timezone.now()
    steps = (("expired", lambda: len(expire_due_orders(now))),
             ("consumed", lambda: consume_paid_orders(now)),
             ("stuck_resolved", lambda: len(resolve_stuck_orders(now))),
             ("releases_retried", retry_pending_releases))
    counts = {}
    for name, step in steps:
        try:
            counts[name] = step()
        except Exception:  # noqa: BLE001 - one broken step must not stop the rest
            logger.exception("Timer step %s failed; it will run again next time.", name)
            counts[name] = None
    return counts
