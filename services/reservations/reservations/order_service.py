"""LP-28: a restaurant accepts or rejects a new order.

The decision is a compare-and-set on the locked order row, so a double click,
two tabs, or a customer cancelling at the same moment can make only one
transition. Rejecting then asks Marketplace to release each line's stock.
Marketplace releases an allocation only from HELD, so retries free the units
exactly once (docs/microservices-plan.md section 6).
"""

from django.db import transaction
from django.utils import timezone

from . import marketplace
from .models import Order

Status = Order.Status
Sync = Order.SyncState

ALREADY = {
    Status.ACCEPTED: "This order was already accepted.",
    Status.REJECTED: "This order was already rejected.",
    Status.CANCELLED: "The customer cancelled this order, so it can't be accepted or rejected.",
    Status.READY: "This order was already accepted and is ready.",
    Status.COLLECTED: "This order was already collected.",
}
REASON_REQUIRED = "Give a reason. The customer will see it."


class OrderNotFound(Exception):
    """No such order for this restaurant (other restaurants' orders look the same)."""


class DecisionRefused(Exception):
    def __init__(self, code, detail):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def decide(order_ref, operator_user_id, decision, reason=""):
    """Accept or reject a Placed order of this restaurant. Returns the order."""
    reason = (reason or "").strip()
    if decision == Status.REJECTED and not reason:
        raise DecisionRefused("reason_required", REASON_REQUIRED)

    with transaction.atomic():
        order = (
            Order.objects.select_for_update()
            .filter(ref=order_ref, shop_operator_user_id=operator_user_id)
            .first()
        )
        if order is None:
            raise OrderNotFound
        if order.status != Status.PLACED:
            raise DecisionRefused("already_decided", ALREADY.get(order.status, "This order can't be changed."))

        order.status = decision
        order.decided_at = timezone.now()
        fields = ["status", "decided_at", "updated_at"]
        if decision == Status.REJECTED:
            order.rejection_reason = reason
            order.sync_state = Sync.RELEASE_PENDING
            fields += ["rejection_reason", "sync_state"]
        order.save(update_fields=fields)

    if decision == Status.REJECTED:
        # Outside the transaction: the decision is already committed, and the
        # release is retried by `manage.py retry_releases` if this fails.
        release_stock(order)
    return order


def release_stock(order):
    """Ask Marketplace to release every line. SYNCED once all are confirmed;
    stays RELEASE_PENDING if Marketplace can't be reached; NEEDS_ATTENTION on
    an answer that should be impossible (for example consumed stock)."""
    try:
        results = [marketplace.release(line.allocation_ref, line.listing_id)[0] for line in order.lines.all()]
    except marketplace.MarketplaceUnavailable:
        return order

    state = Sync.SYNCED if all(status == 200 for status in results) else Sync.NEEDS_ATTENTION
    Order.objects.filter(pk=order.pk, sync_state=Sync.RELEASE_PENDING).update(sync_state=state)
    order.sync_state = state
    return order


def retry_pending_releases():
    """Finish releases whose first attempt didn't reach Marketplace."""
    pending = Order.objects.filter(sync_state=Sync.RELEASE_PENDING).prefetch_related("lines")
    return [release_stock(order) for order in pending]
