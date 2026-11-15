"""LP-27: the customer cancels their own order before its pick-up-by time.

docs/microservices-plan.md §6 "Cancellation and its retries":

1. Under the order's row lock: only the customer who placed it, only while it
   is Reserved (CONFIRMED) and only while now < pick_up_by. The order becomes
   CANCELLED (cancelled_by = customer) with sync_state RELEASE_PENDING, and
   its history row, in one transaction (reservations.orders.change_status).
2. After that commits, each item's LP-20 allocation is released through
   Marketplace's internal API, so the food goes back on sale. Only when every
   release is acknowledged does the order become SYNCED. A release without
   an answer leaves RELEASE_PENDING for the LP-25 retry job; Marketplace's
   release is idempotent, so a retry never frees the units twice.

Pressing Cancel twice: the second press waits for the first one's lock, then
finds the order already Cancelled and is refused, so the food is released by
the first press only.

Cancelling is never a strike: strikes (LP-80) count Expired orders only, and
a cancelled order never becomes Expired.
"""

from django.db import transaction
from django.utils import timezone

from . import marketplace_client as market
from .checkout import item_ref
from .models import VISIBLE_STATUSES, Actor, Order, Status, SyncState
from .orders import change_status

REFUSALS = {
    Status.CANCELLED: ("already_cancelled", "This order is already cancelled."),
    Status.COLLECTED: ("already_paid", "This order was already picked up and paid, so it can't be cancelled."),
    Status.EXPIRED: ("expired", "This order has expired, so it can't be cancelled."),
}


class CancelRefused(Exception):
    """Nothing changed. code/detail (and extra) go into the API answer."""

    def __init__(self, code, detail, http_status, **extra):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.http_status = http_status
        self.extra = extra

    @property
    def body(self):
        return {"code": self.code, "detail": self.detail, **self.extra}


def _too_late(order):
    phone = order.shop_phone
    detail = "The pick-up time has passed, so this order can't be cancelled here any more. "
    detail += f"Please call the restaurant on {phone}." if phone else "Please contact the restaurant."
    return CancelRefused("pickup_time_passed", detail, 409, shop_phone=phone,
                         pick_up_by=order.pick_up_by.isoformat())


def cancel_by_customer(ref, customer_user_id, now=None):
    """Cancel one of the customer's own orders. Returns the order; raises
    CancelRefused when it is not theirs, not Reserved, or too late."""
    with transaction.atomic():
        order = (Order.objects.select_for_update()
                 .filter(reservation_ref=ref, customer_user_id=customer_user_id).first())
        # Someone else's order, or one never shown (PROCESSING, NOT_RESERVED):
        # 404, as for reading it (LP-78), so a reference reveals nothing.
        if order is None or order.status not in VISIBLE_STATUSES:
            raise CancelRefused("not_found", "No such order.", 404)
        if order.status != Status.CONFIRMED:
            code, detail = REFUSALS[Status(order.status)]
            raise CancelRefused(code, detail, 409, status=order.status)
        moment = now or timezone.now()
        if moment >= order.pick_up_by:
            raise _too_late(order)
        order = change_status(order, Status.CANCELLED, actor=Actor.CUSTOMER, reason="Cancelled by the customer",
                              sync_state=SyncState.RELEASE_PENDING, expected_status=Status.CONFIRMED,
                              now=moment)

    # Outside the transaction: the cancellation is committed before
    # Marketplace is contacted (plan §6). Every item is tried, even after one
    # fails, so as much food as possible goes back on sale straight away.
    released = [market.release(item_ref(order, line.listing_id), line.listing_id) for line in order.lines.all()]
    if all(released):
        # Conditional, so it never overwrites a state set by someone else meanwhile.
        Order.objects.filter(pk=order.pk, sync_state=SyncState.RELEASE_PENDING).update(sync_state=SyncState.SYNCED)
    return Order.objects.prefetch_related("lines").get(pk=order.pk)
