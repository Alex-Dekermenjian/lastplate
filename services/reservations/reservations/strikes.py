"""LP-80: expired-order strikes. Stop people who reserve and never come.

Payment is cash at the counter, so there is no deposit to lose. Instead:

- Each Expired order is one strike, dated by its pick-up-by time. Cancelling
  is never a strike (a cancelled order never becomes Expired).
- Only strikes of the last 30 days count. Nothing is stored for a strike:
  it is computed from the orders, so it can never drift. The only extra
  record is a StrikeRemoval, when the admin removes one with a reason.
- 2 strikes: the customer sees a warning.
- 3 strikes within 30 days: no ordering until 7 days after the 3rd strike.
  Browsing still works. The block ends by itself.
"""

from dataclasses import dataclass
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import Order, Status, StrikeRemoval

STRIKE_WINDOW = timedelta(days=30)
WARN_AT = 2
BLOCK_AT = 3
BLOCK_FOR = timedelta(days=7)


@dataclass(frozen=True)
class StrikeStatus:
    count: int
    blocked_until: object  # aware datetime, or None

    @property
    def warning(self):
        return self.count >= WARN_AT

    def as_dict(self):
        return {
            "count": self.count,
            "warning": self.warning,
            "blocked_until": self.blocked_until.isoformat() if self.blocked_until else None,
            "warn_at": WARN_AT,
            "block_at": BLOCK_AT,
        }


class StrikeRefused(Exception):
    def __init__(self, code, detail, http_status):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.http_status = http_status

    @property
    def body(self):
        return {"code": self.code, "detail": self.detail}


def strike_orders(customer_user_id):
    """Every Expired order of the customer whose strike was not removed."""
    return Order.objects.filter(customer_user_id=customer_user_id, status=Status.EXPIRED,
                                strike_removal__isnull=True)


def _strike_times(customer_user_id, since, now):
    return sorted(strike_orders(customer_user_id).filter(pick_up_by__gt=since, pick_up_by__lte=now)
                  .values_list("pick_up_by", flat=True))


def strike_status(customer_user_id, now=None):
    now = now or timezone.now()
    # A block can still be running from a 3rd strike up to 7 days ago, whose
    # two earlier strikes were up to 30 days before that.
    times = _strike_times(customer_user_id, now - BLOCK_FOR - STRIKE_WINDOW, now)
    count = sum(1 for moment in times if moment > now - STRIKE_WINDOW)
    blocked_until = None
    for index, moment in enumerate(times):
        # This strike was (at least) the 3rd within 30 days: blocked for 7 days from it.
        in_window = [t for t in times[:index + 1] if t > moment - STRIKE_WINDOW]
        if len(in_window) >= BLOCK_AT and moment + BLOCK_FOR > now:
            blocked_until = max(blocked_until or moment, moment + BLOCK_FOR)
    return StrikeStatus(count=count, blocked_until=blocked_until)


def remove_strike(ref, admin_user_id, reason, now=None):
    """The admin removes one Expired order's strike, with a reason."""
    reason = (reason or "").strip() if isinstance(reason, str) else ""
    if not reason:
        raise StrikeRefused("reason_required", "Say why the strike is removed.", 400)
    with transaction.atomic():
        order = Order.objects.select_for_update().filter(reservation_ref=ref).first()
        if order is None or order.status != Status.EXPIRED:
            raise StrikeRefused("not_found", "No expired order with this reference.", 404)
        if StrikeRemoval.objects.filter(order=order).exists():
            raise StrikeRefused("already_removed", "This strike was already removed.", 409)
        try:
            with transaction.atomic():
                return StrikeRemoval.objects.create(order=order, reason=reason[:1000],
                                                    removed_by_user_id=admin_user_id,
                                                    created_at=now or timezone.now())
        except IntegrityError:
            raise StrikeRefused("already_removed", "This strike was already removed.", 409) from None


def customers_with_strikes(now=None):
    """[(customer_user_id, first name)] with at least one strike in the last
    30 days or a block still running, most recent strike first."""
    now = now or timezone.now()
    rows = (Order.objects.filter(status=Status.EXPIRED, strike_removal__isnull=True,
                                 pick_up_by__gt=now - BLOCK_FOR - STRIKE_WINDOW, pick_up_by__lte=now)
            .order_by("-pick_up_by").values_list("customer_user_id", "customer_first_name"))
    seen = {}
    for customer_user_id, name in rows:
        seen.setdefault(customer_user_id, name or "Customer")
    return list(seen.items())
