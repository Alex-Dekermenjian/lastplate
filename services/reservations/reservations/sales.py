"""LP-33: read-only reporting from agreed order snapshots."""
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.utils import timezone

from .marketplace_client import MarketplaceUnavailable, operator_shop
from .models import Order, Status, VISIBLE_STATUSES

ZONE = ZoneInfo("Asia/Beirut")
PAGE_SIZE = 50


class ReportRefused(Exception):
    def __init__(self, code, detail, status=400):
        self.body = {"code": code, "detail": detail}
        self.status = status


def owned_shop(user_id):
    try:
        status, shop = operator_shop(user_id)
        if status == 404:
            raise ReportRefused("not_found", "No shop found.", 404)
        if status != 200 or not isinstance(shop, dict) or type(shop.get("id")) is not int \
                or shop["id"] <= 0 or not isinstance(shop.get("branches"), list) or any(
                    not isinstance(b, dict) or type(b.get("id")) is not int or b["id"] <= 0
                    or not isinstance(b.get("name"), str) for b in shop["branches"]):
            raise MarketplaceUnavailable()
        return shop
    except MarketplaceUnavailable:
        raise ReportRefused("unavailable", "Can't load sales right now. Please try again.", 503) from None


def money(value):
    return format(value.quantize(Decimal("0.01")), ".2f")


def report(shop, params, *, now=None, export=False):
    now = now or timezone.now()
    today = now.astimezone(ZONE).date()
    try:
        dates = []
        for key in ("from", "to"):
            raw = params.get(key) or today.isoformat()
            parsed = date.fromisoformat(raw)
            if parsed.isoformat() != raw:
                raise ValueError()
            dates.append(parsed)
        first, last = dates
        if first > last:
            raise ValueError()
        start = datetime.combine(first, time.min, ZONE)
        end = datetime.combine(last + timedelta(days=1), time.min, ZONE)
    except (ValueError, TypeError, OverflowError):
        raise ReportRefused("invalid_dates", "Enter a valid From and To date (YYYY-MM-DD), in order.") from None
    branches = {b["id"]: b["name"] for b in shop["branches"]}
    branch = params.get("branch", "")
    if branch:
        try:
            branch = int(branch)
        except (ValueError, TypeError):
            branch = None
        if branch not in branches:
            raise ReportRefused("not_found", "No such branch.", 404)
    else:
        branch = None
    try:
        page = 1 if export else int(params.get("page", "1"))
        if page < 1:
            raise ValueError()
    except (ValueError, TypeError):
        raise ReportRefused("invalid_page", "Page must be a positive integer.") from None
    orders = Order.objects.visible_to_users().filter(
        shop_id=shop["id"], branch_id__in=branches, pick_up_by__gte=start, pick_up_by__lt=end)
    if branch is not None:
        orders = orders.filter(branch_id=branch)
    # Fetch lines in batches: no joins that multiply order counts.
    rows = []
    counts = {s: 0 for s in VISIBLE_STATUSES}
    collected, expected, saved = Decimal("0.00"), Decimal("0.00"), 0
    total_count = 0
    for order in orders.prefetch_related("lines").order_by("-pick_up_by", "-id").iterator(chunk_size=500):
        status = order.status
        if status == Status.CONFIRMED and order.pick_up_by <= now:
            status = Status.EXPIRED
        lines = list(order.lines.all())
        amount = sum((line.unit_price * line.quantity for line in lines), Decimal("0.00"))
        quantity = sum(line.quantity for line in lines)
        paid = amount if status == Status.COLLECTED else Decimal("0.00")
        due = amount if status == Status.CONFIRMED else Decimal("0.00")
        items = quantity if status == Status.COLLECTED else 0
        counts[status] += 1
        collected += paid
        expected += due
        saved += items
        if export or (page - 1) * PAGE_SIZE <= total_count < page * PAGE_SIZE:
            rows.append({
                "reservation_ref": str(order.reservation_ref), "branch_id": order.branch_id,
                "branch_name": order.branch_name or branches[order.branch_id],
                "status": status, "status_label": VISIBLE_STATUSES[status],
                "pick_up_by": order.pick_up_by.astimezone(ZONE).isoformat(),
                "collected_at": order.collected_at.astimezone(ZONE).isoformat() if order.collected_at else None,
                "quantity": quantity, "order_total": money(amount), "cash_collected": money(paid),
                "cash_expected": money(due), "items_saved": items,
            })
        total_count += 1
    return {
        "currency": "USD", "timezone": "Asia/Beirut", "date_basis": "pick_up_by",
        "filters": {"from": first.isoformat(), "to": last.isoformat(), "branch": branch},
        "branches": shop["branches"],
        "summary": {"orders": total_count, "paid_orders": counts[Status.COLLECTED],
                    "reserved_orders": counts[Status.CONFIRMED], "cancelled_orders": counts[Status.CANCELLED],
                    "expired_orders": counts[Status.EXPIRED], "cash_collected": money(collected),
                    "cash_expected": money(expected), "items_saved": saved},
        "pagination": {"page": page, "page_size": PAGE_SIZE, "total": total_count}, "orders": rows,
    }
