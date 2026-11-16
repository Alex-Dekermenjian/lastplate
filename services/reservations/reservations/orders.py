"""LP-17: the only way to create an order and to change its status.

change_status is the single function that moves an order between statuses.
It locks the order row (compare-and-set, docs/microservices-plan.md §6) and
writes the OrderEvent in the same transaction, so the history can never miss
a change or record one that did not happen.
"""

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from .models import (
    OPEN_STATUSES,
    PICKUP_CODE_ALPHABET,
    PICKUP_CODE_LENGTH,
    Actor,
    CancelledBy,
    Order,
    OrderEvent,
    OrderLine,
    Status,
)

BEIRUT = ZoneInfo("Asia/Beirut")

# Allowed ordinary moves (§6). LP-24 adds an explicit guarded Paid undo below.
ALLOWED_CHANGES = {
    Status.PROCESSING: {Status.CONFIRMED, Status.NOT_RESERVED, Status.CANCELLED, Status.EXPIRED},
    Status.CONFIRMED: {Status.CANCELLED, Status.COLLECTED, Status.EXPIRED},
}
# Who may cancel, and which cancelled_by value that records.
CANCELLERS = {Actor.CUSTOMER: CancelledBy.CUSTOMER, Actor.SHOP: CancelledBy.SHOP, Actor.ADMIN: CancelledBy.ADMIN}
# Collecting means the restaurant was paid in cash at the counter.
COLLECTORS = {Actor.SHOP, Actor.ADMIN}

# Attempts at a free pickup code before giving up. With 32^6 (about a
# billion) codes and a handful of open orders per branch, one is almost always enough.
PICKUP_CODE_ATTEMPTS = 20


class InvalidStatusChange(Exception):
    """The order is not in a status that allows this change."""


@dataclass(frozen=True)
class LineSnapshot:
    """What the offer looked like when the customer placed the order."""

    listing_id: int
    quantity: int
    title: str
    unit_price: Decimal
    pickup_start: datetime
    pickup_end: datetime


def pick_up_by(created_at, lines, hold_minutes=None):
    """min(max(created_at, earliest start) + hold, earliest end).

    1 hour (PICKUP_HOLD_MINUTES) after the order is placed, or after the pickup
    window opens if it was placed earlier, but never later than the end of
    the window.
    """
    if not lines:
        raise ValueError("An order needs at least one item.")
    hold = timedelta(minutes=settings.PICKUP_HOLD_MINUTES if hold_minutes is None else hold_minutes)
    earliest_start = min(line.pickup_start for line in lines)
    earliest_end = min(line.pickup_end for line in lines)
    return min(max(created_at, earliest_start) + hold, earliest_end)


def new_pickup_code():
    return "".join(secrets.choice(PICKUP_CODE_ALPHABET) for _ in range(PICKUP_CODE_LENGTH))


def _code_is_taken(branch_id, code):
    return Order.objects.filter(branch_id=branch_id, pickup_code=code).filter(
        Q(status__in=OPEN_STATUSES) | Q(status=Status.COLLECTED, sync_state="CONSUME_PENDING",
                                      consumption_started_at__isnull=True)).exists()


PLACE_FIELDS = {"shop_name": 200, "branch_name": 100, "pickup_address": 255, "shop_phone": 150}


def _place_fields(place):
    """LP-78: the saved pickup place, trimmed to the column sizes."""
    place = place or {}
    return {name: str(place.get(name) or "").strip()[:size] for name, size in PLACE_FIELDS.items()}


def create_order(*, customer_user_id, shop_id, branch_id, request_key, lines, now=None, place=None, customer_first_name="", customer_phone=""):
    """Store a new PROCESSING order with its item copies and first history row.

    LP-78: `place` (shop_name, branch_name, pickup_address, shop_phone) is
    saved with the order, so later changes in Marketplace never rewrite it.

    Contacting Marketplace for stock is the next step (LP-19/LP-20); this only
    stores the row that must exist before that call (§6, step 2).
    """
    lines = [line if isinstance(line, LineSnapshot) else LineSnapshot(**line) for line in lines]
    if not lines:
        raise ValueError("An order needs at least one item.")
    for line in lines:
        if timezone.is_naive(line.pickup_start) or timezone.is_naive(line.pickup_end):
            raise ValueError("Pickup times must be timezone-aware.")

    created_at = now or timezone.now()
    total = sum((Decimal(line.unit_price) * line.quantity for line in lines), Decimal("0"))

    for _ in range(PICKUP_CODE_ATTEMPTS):
        code = new_pickup_code()
        if _code_is_taken(branch_id, code):
            continue
        try:
            with transaction.atomic():
                order = Order.objects.create(
                    customer_user_id=customer_user_id,
                    customer_first_name=customer_first_name[:150],
                    customer_phone=customer_phone,
                    shop_id=shop_id,
                    branch_id=branch_id,
                    request_key=request_key,
                    pickup_code=code,
                    total_cash=total.quantize(Decimal("0.01")),
                    created_at=created_at,
                    pick_up_by=pick_up_by(created_at, lines),
                    **_place_fields(place),
                )
                OrderLine.objects.bulk_create(
                    OrderLine(
                        order=order,
                        listing_id=line.listing_id,
                        quantity=line.quantity,
                        title=line.title,
                        unit_price=line.unit_price,
                        pickup_start=line.pickup_start,
                        pickup_end=line.pickup_end,
                    )
                    for line in lines
                )
                OrderEvent.objects.create(
                    order=order, from_status="", to_status=Status.PROCESSING,
                    actor=Actor.CUSTOMER, reason="Order placed", created_at=created_at,
                )
            return order
        except IntegrityError:
            # Another order of this branch took the same code at the same
            # moment; any other integrity error is a real fault.
            if not _code_is_taken(branch_id, code):
                raise
    raise RuntimeError("Could not find a free pickup code for this branch.")


def change_status(order, to_status, *, actor, reason="", sync_state=None, expected_status=None, now=None, undo_collection=False):
    """Move an order to another status and record it in its history.

    Locks the row, checks the change is allowed from the status it has now
    (and, if given, that it is still expected_status), then saves the order
    and its OrderEvent in one transaction. Returns the updated order.
    """
    to_status = Status(to_status)
    actor = Actor(actor)
    reason = (reason or "").strip()
    with transaction.atomic():
        locked = Order.objects.select_for_update().get(pk=order.pk)
        moment = now or timezone.now()
        from_status = Status(locked.status)
        if expected_status is not None and from_status != Status(expected_status):
            raise InvalidStatusChange(f"The order is {from_status.label}, not {Status(expected_status).label}.")
        undo = undo_collection and from_status == Status.COLLECTED and to_status == Status.CONFIRMED
        if undo:
            from .collection import UNDO_WINDOW
            if actor != Actor.SHOP or locked.sync_state != "CONSUME_PENDING" \
                    or locked.consumption_started_at is not None or locked.collected_at is None \
                    or moment < locked.collected_at or moment >= locked.collected_at + UNDO_WINDOW:
                raise InvalidStatusChange("This payment can no longer be undone.")
            locked.collected_at = None
            sync_state = "SYNCED"
        if not undo and to_status not in ALLOWED_CHANGES.get(from_status, set()):
            raise InvalidStatusChange(f"An order that is {from_status.label} cannot become {to_status.label}.")

        if to_status == Status.CANCELLED:
            if actor not in CANCELLERS:
                raise InvalidStatusChange("Only the customer, the restaurant or an admin can cancel an order.")
            if actor in (Actor.SHOP, Actor.ADMIN) and not reason:
                raise InvalidStatusChange("Say why the order is cancelled.")
            locked.cancelled_by = CANCELLERS[actor]
            locked.cancel_reason = reason
        if to_status == Status.COLLECTED:
            if actor not in COLLECTORS:
                raise InvalidStatusChange("Only the restaurant or an admin can mark an order as paid.")
            locked.collected_at = moment

        locked.status = to_status
        if sync_state is not None:
            locked.sync_state = sync_state
        locked._status_change_allowed = True
        try:
            locked.save(update_fields=["status", "sync_state", "cancelled_by", "cancel_reason", "collected_at"])
        finally:
            locked._status_change_allowed = False
        OrderEvent.objects.create(
            order=locked, from_status=from_status, to_status=to_status,
            actor=actor, reason=reason, created_at=moment,
        )
    return locked


def beirut(moment):
    """An aware datetime in Beirut time, for anything shown to people."""
    return timezone.localtime(moment, BEIRUT)


def _day(moment):
    return f"{moment:%a} {moment.day} {moment:%b}"


def pickup_window_text(start, end):
    """Same wording as the web pages: "Thu 19 Nov, 18:00–21:00" (Beirut time)."""
    start, end = beirut(start), beirut(end)
    if start.date() == end.date():
        return f"{_day(start)}, {start:%H:%M}–{end:%H:%M}"
    return f"{_day(start)}, {start:%H:%M} – {_day(end)}, {end:%H:%M}"


def time_text(moment):
    """For example "Thu 19 Nov, 19:15" (Beirut time)."""
    if moment is None:
        return "—"
    moment = beirut(moment)
    return f"{_day(moment)}, {moment:%H:%M}"
