"""LP-19: place an order from the customer's cart.

docs/microservices-plan.md §6 "Creating a reservation", for a cart of one or
more items from one branch:

1. Recheck every item with Marketplace's public offer API (still on sale,
   enough left, same price, and within the offer's LP-84 limit per customer).
   Any problem: nothing is saved, and the customer is told exactly what
   changed. Prices and terms always come from Marketplace.
2. Under a per-customer lock: a known request_key returns that same order;
   blocked customers and a 3rd active order are refused; the LP-84 limit is
   counted again (two tabs cannot both get past it); otherwise the order
   is saved as PROCESSING before Marketplace's stock is touched.
3. Outside the transaction, hold each item (one LP-20 allocation per item,
   with a reference derived from the order's reference and the offer ID).
4. All held: the order becomes CONFIRMED ("Reserved"). Any definitive "no":
   the items already held are released and the order becomes NOT_RESERVED,
   which is never shown as an order (all or nothing).
5. No answer (timeout, Marketplace down): the order stays PROCESSING and
   hidden, and the customer is asked to try again. Pressing Reserve again
   sends the same request_key and continues the same order. Until the LP-25
   timer exists, items held for an order that is never retried stay held.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, connection, transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from . import marketplace_client as market
from .models import Actor, Order, OrderLine, Status, SyncState
from .orders import InvalidStatusChange, LineSnapshot, change_status, create_order

MAX_ACTIVE_ORDERS = 2
MAX_LINES = 20
MAX_QUANTITY = 100
# Orders that count towards MAX_ACTIVE_ORDERS: Reserved, and ones still being placed.
ACTIVE_STATUSES = (Status.PROCESSING, Status.CONFIRMED)
# LP-84: orders that use up a customer's limit on an offer: being placed,
# Reserved, and Paid (already collected). Cancelled, Expired and Not reserved
# orders give their units back, so the customer may reserve them again.
LIMIT_STATUSES = (Status.PROCESSING, Status.CONFIRMED, Status.COLLECTED)

# What each Marketplace refusal means for the customer.
REJECT_MESSAGES = {
    "not_published": ("taken_down", "This offer was taken down."),
    "shop_not_approved": ("taken_down", "This offer was taken down."),
    "shop_suspended": ("taken_down", "This offer was taken down."),  # LP-60
    "pickup_ended": ("pickup_ended", "Pickup ended."),
    "terms_changed": ("offer_changed", "This offer changed. Please check it again."),
}


class CheckoutRefused(Exception):
    """Nothing was reserved. code/detail/problems go into the API answer."""

    def __init__(self, code, detail, http_status, problems=None):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.http_status = http_status
        self.problems = problems or []


class TryAgain(Exception):
    """Marketplace gave no answer; the outcome is unknown."""

    def __init__(self, detail, order=None):
        super().__init__(detail)
        self.detail = detail
        self.order = order


@dataclass
class CheckoutResult:
    order: Order
    created: bool


@dataclass
class _Item:
    listing_id: int
    quantity: int
    expected_unit_price: Decimal | None
    offer: dict = field(default_factory=dict)


def customer_is_blocked(customer_user_id):
    """When the customer may order again (an aware datetime), or None if they
    may order now. LP-80: 3 expired orders within 30 days block ordering for
    7 days after the 3rd. LP-60: or the admin blocked them until a date."""
    from .blocks import ordering_block
    return ordering_block(customer_user_id)[0]


def blocked_detail(until, customer_user_id=None):
    from .blocks import WHY_ADMIN, ordering_block
    from .orders import time_text
    if not isinstance(until, datetime):
        return "Your account can't place orders right now. You can still browse."
    if customer_user_id is not None and ordering_block(customer_user_id)[1] == WHY_ADMIN:
        return (f"LastPlate has blocked ordering on your account until {time_text(until)}. "
                "You can still browse.")
    return (f"You can't place orders until {time_text(until)} because 3 of your orders expired "
            "without being picked up. You can still browse.")


def item_ref(order, listing_id):
    """The allocation reference for one item of an order: always the same for
    the same order and offer, so a retry never holds stock twice."""
    return uuid.uuid5(order.reservation_ref, f"listing:{int(listing_id)}")


def _problem(item, code, detail, **extra):
    return {"listing_id": item.listing_id, "title": item.offer.get("title") or "", "code": code,
            "detail": detail, **extra}


def _money(value):
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _moment(value):
    moment = parse_datetime(value) if isinstance(value, str) else None
    return moment if isinstance(moment, datetime) and moment.tzinfo is not None else None


def parse_items(lines):
    """The cart lines sent by the web: listing_id, quantity, expected_unit_price."""
    if not isinstance(lines, list) or not lines:
        raise CheckoutRefused("invalid_request", "The cart is empty.", 400)
    if len(lines) > MAX_LINES:
        raise CheckoutRefused("invalid_request", f"A cart holds at most {MAX_LINES} different items.", 400)
    items, seen = [], set()
    for line in lines:
        if not isinstance(line, dict):
            raise CheckoutRefused("invalid_request", "Each cart line must be an object.", 400)
        listing_id, quantity = line.get("listing_id"), line.get("quantity")
        for name, value, maximum in (("listing_id", listing_id, None), ("quantity", quantity, MAX_QUANTITY)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1 or (maximum and value > maximum):
                raise CheckoutRefused("invalid_request", f"{name} must be a whole number from 1"
                                      + (f" to {maximum}." if maximum else "."), 400)
        if listing_id in seen:
            raise CheckoutRefused("invalid_request", "Each offer may appear only once in the cart.", 400)
        seen.add(listing_id)
        expected = line.get("expected_unit_price")
        items.append(_Item(listing_id, quantity, None if expected is None else _money(expected)))
    return items


def _limit_of(offer):
    """LP-84: the offer's most units per customer, or None when it has no limit."""
    limit = offer.get("max_per_customer")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        return None
    return limit


def _limit_problems(customer_user_id, items):
    """LP-84: one problem for each item that would take the customer above the
    offer's limit, counting what they already reserved or collected from the
    same offer in other orders. Allowed is how many more they may still take."""
    limited = [item for item in items if _limit_of(item.offer) is not None]
    if not limited:
        return []
    totals = OrderLine.objects.filter(
        order__customer_user_id=customer_user_id,
        order__status__in=LIMIT_STATUSES,
        listing_id__in=[item.listing_id for item in limited],
    ).values("listing_id").annotate(total=Sum("quantity"))
    have = {row["listing_id"]: row["total"] for row in totals}

    problems = []
    for item in limited:
        limit, already = _limit_of(item.offer), have.get(item.listing_id, 0)
        allowed = max(limit - already, 0)
        if item.quantity <= allowed:
            continue
        if already == 0:
            detail = f"Limit of {limit} per customer."
        elif allowed == 0:
            detail = f"You have reached the limit of {limit} per customer for this offer."
        else:
            detail = f"Limit of {limit} per customer: you can reserve {allowed} more."
        problems.append(_problem(item, "over_limit", detail, allowed=allowed, limit=limit))
    return problems


def _recheck(items, now, customer_user_id=None):
    """Step 1: every item is still on sale, with enough left, at the same price,
    and within the offer's limit per customer (LP-84)."""
    problems = []
    for item in items:
        try:
            status, offer = market.read_offer(item.listing_id)
        except market.MarketplaceUnavailable:
            raise TryAgain("We can't reach the food service right now. Nothing was reserved; please try again.")
        if status != 200 or offer is None:
            problems.append(_problem(item, "not_available", "This offer is no longer available."))
            continue
        item.offer = offer
        free = int(offer.get("free_quantity") or 0)
        price = _money(offer.get("discounted_price"))
        end = _moment(offer.get("pickup_end"))
        if price is None or end is None or _moment(offer.get("pickup_start")) is None \
                or not isinstance(offer.get("shop"), dict) or offer["shop"].get("id") is None:
            problems.append(_problem(item, "not_available", "This offer is no longer available."))
        elif end <= now:
            problems.append(_problem(item, "pickup_ended", "Pickup ended."))
        elif free < item.quantity:
            problems.append(_problem(item, "only_n_left", f"Only {free} left." if free else "Sold out.",
                                     free_quantity=free))
        elif item.expected_unit_price is not None and price != item.expected_unit_price:
            problems.append(_problem(item, "price_changed", f"The price changed to ${price}.",
                                     unit_price=str(price)))

    if customer_user_id is not None:
        # An item that already has a problem keeps that one, not a second.
        flagged = {problem["listing_id"] for problem in problems}
        problems += _limit_problems(customer_user_id, [i for i in items if i.offer and i.listing_id not in flagged])

    places = {(i.offer.get("shop", {}).get("id"), (i.offer.get("branch") or {}).get("id")) for i in items if i.offer}
    if len(places) > 1:
        problems.append({"listing_id": None, "title": "", "code": "several_places",
                         "detail": "An order holds food from one restaurant branch only."})
    if problems:
        raise CheckoutRefused("cart_changed", "Something in your cart changed. Nothing was reserved.", 409, problems)


def _snapshots(items):
    snapshots = []
    for item in items:
        offer = item.offer
        snapshots.append(LineSnapshot(
            listing_id=item.listing_id,
            quantity=item.quantity,
            title=(offer.get("title") or "")[:200],
            unit_price=_money(offer.get("discounted_price")),
            pickup_start=_moment(offer.get("pickup_start")),
            pickup_end=_moment(offer.get("pickup_end")),
        ))
    return snapshots


def place_from_offer(offer):
    """LP-78: where to collect, as Marketplace showed it at checkout."""
    shop = offer.get("shop") if isinstance(offer.get("shop"), dict) else {}
    branch = offer.get("branch") if isinstance(offer.get("branch"), dict) else {}
    several = (shop.get("branch_count") or 1) > 1
    return {
        "shop_name": shop.get("name") or "",
        # Named only when the restaurant has several branches, as on every page.
        "branch_name": (branch.get("name") or "") if several else "",
        "pickup_address": branch.get("address") or offer.get("pickup_address") or "",
        "shop_phone": shop.get("public_contact") or "",
    }


def _lock_customer(customer_user_id):
    """Held until the transaction ends, so one customer's checkouts (two tabs,
    a double click) are counted and saved one at a time."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [int(customer_user_id)])


def _same_cart(order, items):
    lines = {(line.listing_id, line.quantity) for line in order.lines.all()}
    return lines == {(item.listing_id, item.quantity) for item in items}


def _existing(customer_user_id, request_key, items):
    order = Order.objects.filter(customer_user_id=customer_user_id, request_key=request_key).first()
    if order is not None and not _same_cart(order, items):
        raise CheckoutRefused("request_key_reused",
                              "This checkout was already used for a different cart. Please reload your cart.", 409)
    return order


def _save_processing_order(customer_user_id, request_key, items, now, customer_first_name="", customer_phone=""):
    """Step 2. Returns (order, created)."""
    with transaction.atomic():
        _lock_customer(customer_user_id)
        existing = _existing(customer_user_id, request_key, items)
        if existing is not None:
            return existing, False
        blocked_until = customer_is_blocked(customer_user_id)
        if blocked_until:
            raise CheckoutRefused("customer_blocked", blocked_detail(blocked_until, customer_user_id), 403)
        active = Order.objects.filter(customer_user_id=customer_user_id, status__in=ACTIVE_STATUSES).count()
        if active >= MAX_ACTIVE_ORDERS:
            raise CheckoutRefused(
                "too_many_orders",
                f"You already have {MAX_ACTIVE_ORDERS} orders waiting for pickup. "
                "Pick one up or cancel one before ordering again.", 409)
        # LP-84: counted again under the lock, so two checkouts at once (two
        # tabs) cannot both pass the limit.
        over_limit = _limit_problems(customer_user_id, items)
        if over_limit:
            raise CheckoutRefused("cart_changed", "Something in your cart changed. Nothing was reserved.", 409,
                                  over_limit)
        first = items[0].offer
        order = create_order(
            customer_user_id=customer_user_id,
            shop_id=int(first["shop"]["id"]),
            # Offers made before branches existed have none: stored as 0.
            branch_id=int((first.get("branch") or {}).get("id") or 0),
            request_key=request_key,
            lines=_snapshots(items),
            now=now,
            place=place_from_offer(first),
            customer_first_name=customer_first_name,
            customer_phone=customer_phone,
        )
    return order, True


def _set_sync_state(order, sync_state):
    order.sync_state = sync_state
    order.save(update_fields=["sync_state"])


def _reject_problem(line, answer, offers):
    code = answer.code
    item = _Item(line.listing_id, line.quantity, None, offers.get(line.listing_id, {"title": line.title}))
    if code == "not_enough_stock":
        free = None
        try:
            status, offer = market.read_offer(line.listing_id)
            free = int(offer.get("free_quantity") or 0) if status == 200 and offer else 0
        except market.MarketplaceUnavailable:
            pass
        if free is None:
            return _problem(item, "only_n_left", "Not enough left any more.")
        return _problem(item, "only_n_left", f"Only {free} left." if free else "Sold out.", free_quantity=free)
    public_code, detail = REJECT_MESSAGES.get(code, ("not_available", "This offer is no longer available."))
    return _problem(item, public_code, detail)


def _terms_match(line, allocation):
    terms = (allocation or {}).get("terms") or {}
    return (_money(terms.get("unit_price")) == line.unit_price
            and _moment(terms.get("pickup_start")) == line.pickup_start
            and _moment(terms.get("pickup_end")) == line.pickup_end)


def _give_up(order, held_lines, problems, reason):
    """All or nothing: release what was held; the order is never shown."""
    try:
        order = change_status(order, Status.NOT_RESERVED, actor=Actor.SYSTEM, reason=reason[:500],
                              sync_state=SyncState.RELEASE_PENDING if held_lines else SyncState.SYNCED,
                              expected_status=Status.PROCESSING)
    except InvalidStatusChange:
        order = Order.objects.get(pk=order.pk)
        if order.status != Status.NOT_RESERVED:
            return order
    released = all(market.release(item_ref(order, line.listing_id), line.listing_id) for line in held_lines)
    if held_lines and released:
        _set_sync_state(order, SyncState.SYNCED)
    # If a release got no answer, RELEASE_PENDING stays for the LP-25 job.
    raise CheckoutRefused("cart_changed", "Something in your cart changed. Nothing was reserved.", 409, problems)


def _hold_items(order, offers, now):
    """Steps 3-5 for a PROCESSING order (new, or continued by a retry)."""
    lines = list(order.lines.all())
    held = []
    for line in lines:
        offer = offers.get(line.listing_id) or {}
        answer = market.allocate(item_ref(order, line.listing_id), line.listing_id, line.quantity,
                                 offer.get("terms_revision") or 1)
        if answer.kind == market.HELD:
            if not _terms_match(line, answer.allocation):
                held.append(line)
                item = _Item(line.listing_id, line.quantity, None, offer or {"title": line.title})
                return _give_up(order, held, [_problem(item, "offer_changed",
                                                       "This offer changed. Please check it again.")],
                                "Offer terms changed")
            held.append(line)
        elif answer.kind in (market.REJECTED, market.RELEASED):
            return _give_up(order, held, [_reject_problem(line, answer, offers)],
                            f"Marketplace refused listing {line.listing_id}: {answer.code or answer.kind}")
        elif answer.kind == market.UNKNOWN:
            raise TryAgain("We couldn't confirm your order yet. Please press Reserve again.", order)
        else:
            # A set-up or contract fault: staff must look (plan §6).
            _set_sync_state(order, SyncState.NEEDS_ATTENTION)
            raise TryAgain("Something went wrong on our side. Please try again in a few minutes.", order)

    if timezone.now() >= min(line.pickup_end for line in lines):
        # A late answer after the window closed (plan §6, case 1).
        return _give_up(order, held, [{"listing_id": None, "title": "", "code": "pickup_ended",
                                       "detail": "Pickup ended."}], "Pickup ended before confirmation")
    try:
        return change_status(order, Status.CONFIRMED, actor=Actor.SYSTEM, sync_state=SyncState.SYNCED,
                             reason="Stock held", expected_status=Status.PROCESSING)
    except InvalidStatusChange:
        # A parallel retry of the same checkout got there first.
        return Order.objects.get(pk=order.pk)


def place_order(customer_user_id, request_key, lines, now=None, customer_first_name="", customer_phone=""):
    """Place (or continue) one checkout. Returns CheckoutResult; raises
    CheckoutRefused (nothing reserved) or TryAgain (outcome unknown)."""
    request_key = (request_key or "").strip()
    try:
        request_key = str(uuid.UUID(request_key))
    except ValueError:
        raise CheckoutRefused("invalid_request", "request_key must be a UUID.", 400) from None
    items = parse_items(lines)
    now = now or timezone.now()

    # A retry of a checkout that already finished or is in progress.
    existing = _existing(customer_user_id, request_key, items)
    if existing is not None and existing.status != Status.PROCESSING:
        return _finished(existing)

    if existing is None:
        _recheck(items, now, customer_user_id)
        try:
            order, created = _save_processing_order(customer_user_id, request_key, items, now, customer_first_name, customer_phone)
        except IntegrityError:
            # The same checkout was saved a moment ago by a parallel request.
            order, created = _existing(customer_user_id, request_key, items), False
            if order is None:
                raise
        if not created and order.status != Status.PROCESSING:
            return _finished(order)
        offers = {item.listing_id: item.offer for item in items}
    else:
        order, created = existing, False
        offers = {}
        for item in items:
            try:
                status, offer = market.read_offer(item.listing_id)
            except market.MarketplaceUnavailable:
                raise TryAgain("We couldn't confirm your order yet. Please press Reserve again.", order)
            offers[item.listing_id] = offer if status == 200 and offer else {}
        offers = _revisions_for_retry(order, offers)

    order = _hold_items(order, offers, now)
    if order.status == Status.CONFIRMED:
        return CheckoutResult(order, created)
    return _finished(order)


def _revisions_for_retry(order, offers):
    """On a retry the item may no longer be listed (fully held by this very
    order). Its allocation already exists, so any revision returns it."""
    for line in order.lines.all():
        offers.setdefault(line.listing_id, {})
        offers[line.listing_id].setdefault("terms_revision", 1)
    return offers


def _finished(order):
    if order.status == Status.CONFIRMED:
        return CheckoutResult(order, False)
    if order.status == Status.PROCESSING:
        raise TryAgain("We couldn't confirm your order yet. Please press Reserve again.", order)
    last = order.events.order_by("-created_at", "-id").first()
    raise CheckoutRefused("not_reserved", "This order could not be placed. Nothing was reserved.", 409,
                          [{"listing_id": None, "title": "", "code": "not_reserved",
                            "detail": (last.reason if last else "") or "Nothing was reserved."}])
