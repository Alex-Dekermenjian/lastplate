"""LP-43: what the LastPlate admin sees of every order.

- order_list(): every order customers and restaurants see, newest first,
  filtered by status, restaurant and day (Lebanon time) or found by its code.
- revenue(): money from Paid orders today, this week (since Monday) and all
  time; the number of orders, items saved and expired orders; and the same
  numbers per restaurant, highest revenue first, with restaurant cancellations.
  Revenue counts only Paid orders, on the day they were paid.
- attention(): orders whose last Marketplace call got an impossible answer
  (sync_state NEEDS_ATTENTION), so the two services disagree.
- retry(): puts one of those back on the step that failed and runs it once,
  with the same code the timer job uses. Statuses never change here.

Reads only this service's own tables.
"""

import logging
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.utils import timezone

from . import timers
from .models import CancelledBy, Order, OrderLine, Status, SyncState, VISIBLE_STATUSES

logger = logging.getLogger("reservations.admin")

PAGE_SIZE = 50


class AdminRefused(Exception):
    def __init__(self, http_status, code, detail):
        super().__init__(detail)
        self.http_status = http_status
        self.body = {"code": code, "detail": detail}


def _money(value):
    return f"{(value or Decimal('0')):.2f}"


def _midnight(day):
    """Start of a day in Lebanon time (TIME_ZONE is Asia/Beirut)."""
    return timezone.make_aware(datetime.combine(day, time.min))


def _iso(moment):
    return moment.isoformat() if moment else None


def shop_names():
    """The latest name each restaurant had on an order."""
    names = {}
    for shop_id, name in Order.objects.exclude(shop_name="").order_by("created_at", "id") \
            .values_list("shop_id", "shop_name"):
        names[shop_id] = name
    return names


def _shop_name(shop_id, names):
    return names.get(shop_id) or f"Restaurant #{shop_id}"


def order_data(order, names=None):
    return {
        "reservation_ref": str(order.reservation_ref),
        "pickup_code": order.pickup_code,
        "status": order.status,
        "status_label": order.status_label or order.get_status_display(),
        "sync_state": order.sync_state,
        "needs_attention": order.sync_state == SyncState.NEEDS_ATTENTION,
        "created_at": _iso(order.created_at),
        "pick_up_by": _iso(order.pick_up_by),
        "collected_at": _iso(order.collected_at),
        "shop_id": order.shop_id,
        "shop_name": order.shop_name or _shop_name(order.shop_id, names or {}),
        "branch_name": order.branch_name,
        "customer_user_id": order.customer_user_id,
        "customer_first_name": order.customer_first_name or "Customer",
        "total_cash": _money(order.total_cash),
        "items": [{"title": line.title, "quantity": line.quantity} for line in order.lines.all()],
        "cancelled_by": order.cancelled_by,
        "cancel_reason": order.cancel_reason,
    }


# ---------------------------------------------------------------- all orders

def _positive_int(value, what):
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = 0
    if number < 1:
        raise AdminRefused(400, "bad_filter", f"{what} must be a whole number.")
    return number


def order_list(params):
    orders = Order.objects.visible_to_users()

    status = (params.get("status") or "").strip().upper()
    if status:
        if status not in VISIBLE_STATUSES:
            raise AdminRefused(400, "bad_filter", "Unknown status.")
        orders = orders.filter(status=status)

    shop = (params.get("shop_id") or "").strip()
    if shop:
        orders = orders.filter(shop_id=_positive_int(shop, "Restaurant"))

    day = (params.get("date") or "").strip()
    if day:
        try:
            start = _midnight(date.fromisoformat(day))
        except ValueError:
            raise AdminRefused(400, "bad_filter", "Give the date as YYYY-MM-DD.") from None
        orders = orders.filter(created_at__gte=start, created_at__lt=_midnight(start.date() + timedelta(days=1)))

    code = "".join((params.get("code") or "").split()).upper()
    if code:
        orders = orders.filter(pickup_code__contains=code)

    count = orders.count()
    pages = max(1, -(-count // PAGE_SIZE))
    page = min(_positive_int(params.get("page") or 1, "Page"), pages)
    names = shop_names()
    rows = orders.order_by("-created_at", "-id").prefetch_related("lines")[(page - 1) * PAGE_SIZE:page * PAGE_SIZE]
    return {
        "orders": [order_data(order, names) for order in rows],
        "count": count, "page": page, "pages": pages,
        # For the restaurant filter: every restaurant that has an order.
        "shops": sorted(({"shop_id": shop_id, "shop_name": _shop_name(shop_id, names)}
                         # order_by(): without it the model's default ordering makes DISTINCT repeat shops.
                         for shop_id in Order.objects.visible_to_users().order_by()
                         .values_list("shop_id", flat=True).distinct()),
                        key=lambda shop: shop["shop_name"].lower()),
        "statuses": [{"status": key, "label": label} for key, label in VISIBLE_STATUSES.items()],
        "needs_attention": Order.objects.filter(sync_state=SyncState.NEEDS_ATTENTION).count(),
    }


# ------------------------------------------------------------------- revenue

def _paid_period(since):
    paid = Order.objects.filter(status=Status.COLLECTED)
    if since is not None:
        paid = paid.filter(collected_at__gte=since)
    totals = paid.aggregate(revenue=Sum("total_cash"), orders=Count("id"))
    items = OrderLine.objects.filter(order__in=paid).aggregate(items=Sum("quantity"))["items"]
    return {"revenue": _money(totals["revenue"]), "paid_orders": totals["orders"], "items_saved": items or 0}


PAID = Q(status=Status.COLLECTED)


def revenue(now=None):
    now = now or timezone.now()
    today = timezone.localdate(now)
    week_start = today - timedelta(days=today.weekday())
    visible = Order.objects.visible_to_users()

    shop_cancelled = Q(status=Status.CANCELLED, cancelled_by=CancelledBy.SHOP)
    per_shop = visible.order_by().values("shop_id").annotate(
        orders=Count("id"),
        paid_orders=Count("id", filter=PAID),
        revenue=Sum("total_cash", filter=PAID),
        expired_orders=Count("id", filter=Q(status=Status.EXPIRED)),
        shop_cancellations=Count("id", filter=shop_cancelled),
    )
    items = dict(OrderLine.objects.filter(order__status=Status.COLLECTED).order_by()
                 .values("order__shop_id").annotate(items=Sum("quantity")).values_list("order__shop_id", "items"))
    names = shop_names()
    shops = sorted(({
        "shop_id": row["shop_id"],
        "shop_name": _shop_name(row["shop_id"], names),
        "revenue": _money(row["revenue"]),
        "orders": row["orders"],
        "paid_orders": row["paid_orders"],
        "items_saved": items.get(row["shop_id"], 0),
        "expired_orders": row["expired_orders"],
        "shop_cancellations": row["shop_cancellations"],
    } for row in per_shop), key=lambda shop: (-Decimal(shop["revenue"]), -shop["orders"], shop["shop_name"].lower()))

    return {
        "today": _paid_period(_midnight(today)),
        "week": _paid_period(_midnight(week_start)),
        "all_time": _paid_period(None),
        "week_start": week_start.isoformat(),
        "orders": visible.count(),
        "expired_orders": visible.filter(status=Status.EXPIRED).count(),
        "shop_cancellations": visible.filter(shop_cancelled).count(),
        "needs_attention": Order.objects.filter(sync_state=SyncState.NEEDS_ATTENTION).count(),
        "shops": shops,
    }


# ----------------------------------------------------------- needs attention

RELEASE = "release"
CONSUME = "consume"
ALLOCATE = "allocate"

PROBLEMS = {
    CONSUME: "Paid, but Marketplace hasn't confirmed the sale.",
    RELEASE: "Marketplace hasn't confirmed this order's food was put back.",
    ALLOCATE: "Marketplace gave an unexpected answer while the order was being placed.",
}


def _failed_step(order):
    """Which Marketplace call this order is stuck on, or None if Retry here
    can't safely redo it (a restaurant's cancellation comes with LP-79)."""
    if order.status == Status.COLLECTED:
        return CONSUME
    if order.status == Status.PROCESSING:
        return ALLOCATE
    if order.status in (Status.EXPIRED, Status.NOT_RESERVED) or (
            order.status == Status.CANCELLED and order.cancelled_by != CancelledBy.SHOP):
        return RELEASE
    return None


def attention():
    orders = Order.objects.filter(sync_state=SyncState.NEEDS_ATTENTION).order_by("created_at", "id") \
        .prefetch_related("lines")
    names = shop_names()
    rows = []
    for order in orders:
        step = _failed_step(order)
        rows.append({**order_data(order, names), "step": step, "can_retry": step is not None,
                     "problem": PROBLEMS.get(step, "Marketplace hasn't confirmed this change.")})
    return {"orders": rows}


# Back to the pending state of the step that failed; the timer job also
# picks it up from there if this one attempt gets no answer.
PENDING = {CONSUME: SyncState.CONSUME_PENDING, RELEASE: SyncState.RELEASE_PENDING,
           ALLOCATE: SyncState.ALLOCATE_PENDING}


def _run(step, order):
    if step == CONSUME:
        timers.consume_order(order.pk)
    elif step == RELEASE:
        timers.release_items(Order.objects.get(pk=order.pk))
    else:
        # A checkout that hit a fault: the normal clean-up (two minutes after
        # it was placed) gives it up and releases anything held.
        timers.resolve_stuck_orders()


def retry(ref, admin_user_id):
    order = Order.objects.filter(reservation_ref=ref).first()
    if order is None:
        raise AdminRefused(404, "not_found", "Order not found.")
    if order.sync_state != SyncState.NEEDS_ATTENTION:
        raise AdminRefused(409, "not_needed", "This order doesn't need attention any more.")
    step = _failed_step(order)
    if step is None:
        raise AdminRefused(409, "cannot_retry", "This order can't be retried from here yet. Ask the developers.")
    if Order.objects.filter(pk=order.pk, sync_state=SyncState.NEEDS_ATTENTION).update(sync_state=PENDING[step]) != 1:
        raise AdminRefused(409, "not_needed", "This order doesn't need attention any more.")
    logger.info("Admin %s retried the %s step of order %s.", admin_user_id, step, order.reservation_ref)
    _run(step, order)

    order = Order.objects.prefetch_related("lines").get(pk=order.pk)
    if order.sync_state == SyncState.SYNCED:
        result = "fixed"
    elif order.sync_state == SyncState.NEEDS_ATTENTION:
        result = "still_failing"
    else:
        result = "waiting"  # no answer yet; the timer job keeps trying
    return {**order_data(order), "step": step, "result": result}
