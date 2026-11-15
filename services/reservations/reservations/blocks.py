"""LP-60: the admin blocks or unblocks a customer from ordering.

A block lasts until the start of a chosen day (Lebanon time) and needs a
reason. It works next to LP-80's strikes: checkout refuses a customer while
either applies (ordering_block). Browsing is never blocked.
"""

from datetime import date, datetime, time

from django.db import transaction
from django.utils import timezone

from .models import CustomerBlock
from .orders import BEIRUT
from .strikes import StrikeRefused, strike_status

MAX_REASON = 1000
WHY_STRIKES = "strikes"
WHY_ADMIN = "admin"


def active_block(customer_user_id, now=None):
    now = now or timezone.now()
    return (CustomerBlock.objects.filter(customer_user_id=customer_user_id, lifted_at__isnull=True,
                                         blocked_until__gt=now).order_by("-blocked_until").first())


def ordering_block(customer_user_id, now=None):
    """(until, why) while the customer may not order, else (None, None).
    When both apply, the later end wins."""
    now = now or timezone.now()
    blocks = []
    strikes_until = strike_status(customer_user_id, now).blocked_until
    if strikes_until:
        blocks.append((strikes_until, WHY_STRIKES))
    admin = active_block(customer_user_id, now)
    if admin is not None:
        blocks.append((admin.blocked_until, WHY_ADMIN))
    return max(blocks, key=lambda block: block[0]) if blocks else (None, None)


def _until(value, now):
    """'2026-11-26' -> the start of that day in Lebanon; it must be after today."""
    try:
        day = value if isinstance(value, date) else date.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise StrikeRefused("invalid_date", "Choose the day the customer may order again.", 400) from None
    if day <= timezone.localtime(now, BEIRUT).date():
        raise StrikeRefused("invalid_date", "Choose a day after today.", 400)
    return datetime.combine(day, time.min, tzinfo=BEIRUT)


def block_customer(customer_user_id, until, reason, admin_user_id, now=None):
    now = now or timezone.now()
    reason = reason.strip() if isinstance(reason, str) else ""
    if not reason:
        raise StrikeRefused("reason_required", "Say why the customer is blocked.", 400)
    blocked_until = _until(until, now)
    with transaction.atomic():
        # A new block replaces the running one; both stay on record.
        CustomerBlock.objects.select_for_update().filter(customer_user_id=customer_user_id,
                                                         lifted_at__isnull=True).update(
            lifted_at=now, lifted_by_user_id=admin_user_id)
        return CustomerBlock.objects.create(customer_user_id=customer_user_id, blocked_until=blocked_until,
                                            reason=reason[:MAX_REASON], blocked_by_user_id=admin_user_id,
                                            created_at=now)


def unblock_customer(customer_user_id, admin_user_id, now=None):
    now = now or timezone.now()
    with transaction.atomic():
        lifted = CustomerBlock.objects.select_for_update().filter(
            customer_user_id=customer_user_id, lifted_at__isnull=True, blocked_until__gt=now,
        ).update(lifted_at=now, lifted_by_user_id=admin_user_id)
    if not lifted:
        raise StrikeRefused("not_blocked", "This customer is not blocked by an admin.", 409)
    return lifted


def block_data(block):
    if block is None:
        return None
    return {"blocked_until": block.blocked_until.isoformat(), "reason": block.reason,
            "blocked_at": block.created_at.isoformat(), "blocked_by_user_id": block.blocked_by_user_id}
