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

from datetime import datetime, time, timedelta

from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from . import marketplace_client as market
from .checkout import item_ref
from .models import VISIBLE_STATUSES, Actor, CancelledBy, Order, Status, SyncState
from .orders import BEIRUT, beirut, change_status

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

    return _tell_marketplace(order, SyncState.RELEASE_PENDING,
                             lambda line: market.release(item_ref(order, line.listing_id), line.listing_id))


def _tell_marketplace(order, pending, call):
    """After the cancellation has committed (plan §6), make one Marketplace
    call per item. Every item is tried, even after one fails. Only when all
    are acknowledged does the order become SYNCED; otherwise it stays
    `pending` for the LP-25 retry job (the calls are idempotent)."""
    done = [call(line) for line in order.lines.all()]
    if all(done):
        # Conditional, so it never overwrites a state set by someone else meanwhile.
        Order.objects.filter(pk=order.pk, sync_state=pending).update(sync_state=SyncState.SYNCED)
    return Order.objects.prefetch_related("lines").get(pk=order.pk)


# ---------------------------------------------------------------------------
# LP-79: the restaurant cancels.
#
# Plan §6 "Collection and shop cancellation": under the order lock, only a
# Reserved (CONFIRMED) order of one of the shop's own branches, before its
# pick-up-by time. It becomes CANCELLED (cancelled_by = shop) with the reason
# and SHOP_CANCEL_PENDING in one transaction. After that commits, each item's
# allocation is shop-cancelled in Marketplace: the food is gone, so the units
# leave stock and are NOT put back on sale.
#
# Shop cancellations are counted per restaurant from these rows (orders are
# never deleted and Cancelled is final), so the count can never drift.
# ---------------------------------------------------------------------------

SHOP_REASONS = ("Food ran out", "Closing early", "Other")


def shop_reason(value):
    """The reason exactly as one of SHOP_REASONS, or CancelRefused (400)."""
    reason = value.strip() if isinstance(value, str) else ""
    if reason not in SHOP_REASONS:
        raise CancelRefused("reason_required", "Choose a reason: " + ", ".join(SHOP_REASONS) + ".", 400,
                            reasons=list(SHOP_REASONS))
    return reason


def today_bounds(now):
    """Start and end of today in Lebanon time, as LP-42's Today's orders uses."""
    today = beirut(now).date()
    start = datetime.combine(today, time.min, tzinfo=BEIRUT)
    return start, datetime.combine(today + timedelta(days=1), time.min, tzinfo=BEIRUT)


def _branch_ids(shop):
    return [branch["id"] for branch in shop["branches"]]


def cancel_by_shop(ref, shop, operator_user_id, reason, now=None):
    """Cancel one Reserved order of this shop. `shop` is the operator's own
    shop from Marketplace ({"id", "branches"}). Returns the order; raises
    CancelRefused when it is another shop's, not Reserved, or too late."""
    reason = shop_reason(reason)
    with transaction.atomic():
        order = (Order.objects.select_for_update()
                 .filter(reservation_ref=ref, shop_id=shop["id"], branch_id__in=_branch_ids(shop)).first())
        # Another shop's order (or one never shown): 404, as for pickup
        # lookups (LP-24), so a reference reveals nothing.
        if order is None or order.status not in VISIBLE_STATUSES:
            raise CancelRefused("not_found", "No such order.", 404)
        if order.status != Status.CONFIRMED:
            code, detail = REFUSALS[Status(order.status)]
            raise CancelRefused(code, detail, 409, status=order.status)
        moment = now or timezone.now()
        if moment >= order.pick_up_by:
            # The customer did not come: it expires (a strike, LP-80) instead.
            raise CancelRefused("pickup_time_passed", "The pick-up time has passed, so this order can't be cancelled.",
                                409, pick_up_by=order.pick_up_by.isoformat())
        order = change_status(order, Status.CANCELLED, actor=Actor.SHOP, reason=reason,
                              sync_state=SyncState.SHOP_CANCEL_PENDING, expected_status=Status.CONFIRMED,
                              now=moment)

    return _tell_marketplace(order, SyncState.SHOP_CANCEL_PENDING, lambda line: market.shop_cancel(
        item_ref(order, line.listing_id), line.listing_id, operator_user_id, reason))


def cancel_all_for_today(shop, branch_id, operator_user_id, reason, now=None):
    """Cancel every Reserved order of one of the shop's branches whose
    pick-up-by is later today, with one reason. Each order is cancelled on
    its own (its own lock and transaction), so one that was paid or
    cancelled meanwhile is skipped. Returns the cancelled orders."""
    reason = shop_reason(reason)
    if type(branch_id) is not int or branch_id not in _branch_ids(shop):
        raise CancelRefused("not_found", "No such branch.", 404)
    moment = now or timezone.now()
    _, end = today_bounds(moment)
    refs = list(Order.objects.filter(shop_id=shop["id"], branch_id=branch_id, status=Status.CONFIRMED,
                                     pick_up_by__gt=moment, pick_up_by__lt=end)
                .order_by("pick_up_by", "id").values_list("reservation_ref", flat=True))
    cancelled = []
    for ref in refs:
        try:
            cancelled.append(cancel_by_shop(ref, shop, operator_user_id, reason, now=moment))
        except CancelRefused:
            continue  # paid, cancelled or expired since the list was read
    return cancelled


def shop_cancellation_counts(shop_id=None):
    """{shop_id: number of orders the restaurant cancelled}, for the admin
    (LP-43). Only shops with at least one cancellation appear."""
    orders = Order.objects.filter(status=Status.CANCELLED, cancelled_by=CancelledBy.SHOP)
    if shop_id is not None:
        orders = orders.filter(shop_id=shop_id)
    rows = orders.order_by().values("shop_id").annotate(count=Count("id")).order_by("shop_id")
    return {row["shop_id"]: row["count"] for row in rows}
