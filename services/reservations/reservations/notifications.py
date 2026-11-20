"""LP-41: in-app notifications about a customer's orders.

Three things are told to the customer, never by email or push:
- the restaurant cancelled the order (with the reason it gave);
- the order expired (it was not picked up in time);
- there are 10 minutes (or fewer) left to pick it up, once per order.

The first two are created by on_status_change(), which orders.change_status
calls in the same transaction that writes the OrderEvent, so a notification
exists exactly when the status change does. The reminder is created by
remind_orders_close_to_deadline(), which the timer (expire_due_orders) runs.

notify() never creates the same notification twice: there is one row per order
and kind (a unique constraint), so a repeated timer run, two processes at once
or a retry all end with one row.
"""

import logging
import math
from datetime import timedelta
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone

from .models import Actor, Notification, NotificationKind, Order, Status

logger = logging.getLogger("reservations.notifications")

BEIRUT = ZoneInfo("Asia/Beirut")
# The reminder is sent once pick_up_by is this close.
REMINDER_MINUTES = 10


def _clock(moment):
    return timezone.localtime(moment, BEIRUT).strftime("%H:%M")


def _order_text(order):
    """"your order from Cedar Bakery", or "your order" for older orders."""
    return f"your order from {order.shop_name}" if order.shop_name else "your order"


def notify(order, kind, message, now=None):
    """Create the notification unless this order already has one of this kind.
    Returns True when a new one was created."""
    with transaction.atomic():
        _, created = Notification.objects.get_or_create(
            order=order, kind=kind,
            defaults={"customer_user_id": order.customer_user_id, "message": message,
                      "created_at": now or timezone.now()},
        )
    return created


def on_status_change(order, from_status, to_status, actor, reason, moment):
    """Called by orders.change_status inside its transaction, after the history
    row is written. Only orders the customer could see (they were Reserved)."""
    if from_status != Status.CONFIRMED:
        return
    if to_status == Status.EXPIRED:
        text = _order_text(order)
        notify(order, NotificationKind.EXPIRED,
               f"{text[0].upper()}{text[1:]} expired because it was not picked up in time.", moment)
    elif to_status == Status.CANCELLED and actor == Actor.SHOP:
        message = f"{order.shop_name or 'The restaurant'} cancelled your order."
        if reason:
            message += f" Reason: {reason}."
        notify(order, NotificationKind.SHOP_CANCELLED, message, moment)


def remind_orders_close_to_deadline(now=None, limit=50):
    """The "10 minutes left" reminder: Reserved orders whose pick_up_by is
    within REMINDER_MINUTES and still in the future, once per order. Returns
    how many reminders were created. Never raises: it must not stop expiry."""
    now = now or timezone.now()
    try:
        due = (Order.objects.filter(status=Status.CONFIRMED, pick_up_by__gt=now,
                                    pick_up_by__lte=now + timedelta(minutes=REMINDER_MINUTES))
               .exclude(notifications__kind=NotificationKind.TEN_MINUTES_LEFT)
               .order_by("id")[:limit])
        created = 0
        for order in due:
            minutes = max(1, math.ceil((order.pick_up_by - now).total_seconds() / 60))
            unit = "minute" if minutes == 1 else "minutes"
            created += notify(
                order, NotificationKind.TEN_MINUTES_LEFT,
                f"{minutes} {unit} left to pick up {_order_text(order)}. Pick up by {_clock(order.pick_up_by)}.",
                now)
        return created
    except Exception:  # noqa: BLE001 - logged; the next run tries again
        logger.exception("Creating pick-up reminders failed; the next run will retry.")
        return 0
