"""LP-24: payment transitions; LP-25 claims consumption through this module."""
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .models import Actor, Order, Status, SyncState
from .orders import change_status

UNDO_WINDOW = timedelta(minutes=10)


class CollectionRefused(Exception):
    def __init__(self, code, detail, status=409, **extra):
        self.body = {"code": code, "detail": detail, **extra}
        self.status = status


def can_undo(order, now=None):
    now = now or timezone.now()
    return (order.status == Status.COLLECTED and order.sync_state == SyncState.CONSUME_PENDING
            and order.consumption_started_at is None and order.collected_at is not None
            and order.collected_at <= now < order.collected_at + UNDO_WINDOW)


def pay_or_undo(ref, shop_id, branch_id, version, *, undo=False):
    with transaction.atomic():
        order = Order.objects.select_for_update().filter(
            reservation_ref=ref, shop_id=shop_id, branch_id=branch_id).first()
        if order is None:
            raise CollectionRefused("not_found", "No order with this code here", 404)
        # Read time AFTER acquiring the lock, including waits behind another actor.
        now = timezone.now()
        target = Status.CONFIRMED if undo else Status.COLLECTED
        if order.collection_version == version + 1 and order.status == target:
            return order  # immediately repeated successful request; no new event/time
        if order.collection_version != version:
            raise CollectionRefused("stale_payment", "This order changed. Look up the code again.")
        if undo:
            if not can_undo(order, now):
                raise CollectionRefused("undo_closed", "This payment can no longer be undone.")
        else:
            if order.status not in (Status.CONFIRMED, Status.EXPIRED):
                raise CollectionRefused("not_reserved", "This order is not Reserved.")
            if now >= order.pick_up_by or order.status == Status.EXPIRED:
                raise CollectionRefused("pickup_expired", "The pickup deadline has passed.",
                                        pick_up_by=order.pick_up_by.isoformat())
            if order.sync_state != SyncState.SYNCED:
                raise CollectionRefused("not_synced", "This order is still being confirmed. Please try again.")
            if order.lines.filter(pickup_start__gt=now).exists():
                raise CollectionRefused("too_early", "The pickup window has not opened yet.")
        updated = change_status(order, target, actor=Actor.SHOP,
                                sync_state=SyncState.SYNCED if undo else SyncState.CONSUME_PENDING,
                                expected_status=order.status, now=now, undo_collection=undo,
                                reason="Payment undone" if undo else "Cash received")
        updated.collection_version = version + 1
        updated.save(update_fields=["collection_version"])
        return updated


def claim_consumption(order_id, now=None):
    """LP-25: commit a permanent undo barrier BEFORE any Marketplace HTTP.

    None means not eligible. Returning an order authorizes idempotent consume
    retries for its lines, using checkout.item_ref. See docs/api/lp24-collection.md.
    """
    with transaction.atomic(durable=True):
        order = Order.objects.select_for_update().get(pk=order_id)
        moment = now or timezone.now()
        if order.status != Status.COLLECTED or order.sync_state != SyncState.CONSUME_PENDING \
                or order.collected_at is None or moment < order.collected_at + UNDO_WINDOW:
            return None
        if order.consumption_started_at is None:
            order.consumption_started_at = moment
            order.save(update_fields=["consumption_started_at"])
        return order
