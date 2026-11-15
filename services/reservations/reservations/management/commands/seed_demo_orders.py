"""LP-77: fill the database with realistic fake orders, for building screens.

Development only (refused unless DJANGO_DEBUG is on). Creates ten orders in
every status customers and restaurants see: Reserved, Paid, Cancelled (by
the customer and by the restaurant) and Expired, each with its items and
history, and pick-up-by times around now.

    python manage.py seed_demo_orders
    python manage.py seed_demo_orders --customer-id 12 --customer-name Rami

The orders use real restaurants and offers when Marketplace lists any (so the
restaurant's and admin's screens show them), otherwise clearly fictional ones.

These orders never touch Marketplace stock: no food is held, sold or put
back, so "left" numbers on offers don't change. Every order is created
already synced with Marketplace, so the timer job never consumes or releases
anything for them. (A Reserved one that later expires asks Marketplace to
release a hold that never existed; Marketplace records that and changes no
stock.) Running the command again adds nothing: each order has a fixed
request key for the chosen customer.
"""

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from reservations import marketplace_client as market
from reservations.models import Actor, Order, Status, SyncState
from reservations.orders import change_status, create_order

DEFAULT_CUSTOMER_ID = 999001
FICTIONAL_ID = 999001
KEY_PREFIX = "seed-demo-"


@dataclass(frozen=True)
class Offer:
    """What an order line copies: from Marketplace, or fictional."""

    listing_id: int
    title: str
    unit_price: Decimal
    shop_id: int
    branch_id: int
    place: dict


FICTIONAL_OFFERS = [
    Offer(FICTIONAL_ID + n, f"SAMPLE (fictional) {title}", Decimal(price), FICTIONAL_ID, FICTIONAL_ID,
          {"shop_name": "SAMPLE Bakery (fictional)", "branch_name": "Hamra",
           "pickup_address": "1 Sample Street, Beirut (fictional)", "shop_phone": "+961 1 000 000"})
    for n, (title, price) in enumerate([("glazed donuts", "1.50"), ("croissant box", "4.50"),
                                        ("evening LastBag", "5.00")])
]

# (request key number, final status, actor, reason, hours ago placed, items: [(offer index, quantity)])
PLAN = [
    (1, Status.CONFIRMED, None, "", 0.2, [(0, 2)]),
    (2, Status.CONFIRMED, None, "", 0.1, [(1, 1), (2, 1)]),
    (3, Status.CONFIRMED, None, "", 0.05, [(2, 3)]),
    (4, Status.COLLECTED, Actor.SHOP, "", 3, [(0, 1)]),
    (5, Status.COLLECTED, Actor.SHOP, "", 26, [(1, 2), (0, 1)]),
    (6, Status.COLLECTED, Actor.SHOP, "", 50, [(2, 1)]),
    (7, Status.CANCELLED, Actor.CUSTOMER, "Changed my plans (fictional)", 5, [(1, 1)]),
    (8, Status.CANCELLED, Actor.SHOP, "We ran out before pickup (fictional)", 28, [(2, 2)]),
    (9, Status.EXPIRED, Actor.SYSTEM, "Not picked up by the pick-up time", 6, [(0, 2)]),
    (10, Status.EXPIRED, Actor.SYSTEM, "Not picked up by the pick-up time", 30, [(1, 1)]),
]


def _price(value):
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        return None


def marketplace_offers(limit=3):
    """Up to `limit` offers from one restaurant branch Marketplace lists
    publicly right now, so all of an order's items share a branch."""
    try:
        status, body = market._request("GET", "/api/listings")
    except market.MarketplaceUnavailable:
        return []
    if status != 200 or not isinstance(body, list):
        return []

    by_branch = {}
    for item in body:
        shop, branch = item.get("shop") or {}, item.get("branch") or {}
        price = _price(item.get("discounted_price"))
        if not (item.get("id") and shop.get("id") and branch.get("id") and price is not None):
            continue
        by_branch.setdefault(branch["id"], []).append(Offer(
            listing_id=int(item["id"]),
            title=(item.get("title") or "Food offer")[:200],
            unit_price=price,
            shop_id=int(shop["id"]),
            branch_id=int(branch["id"]),
            place={"shop_name": shop.get("name") or "", "branch_name": branch.get("name") or "",
                   "pickup_address": branch.get("address") or item.get("pickup_address") or "",
                   "shop_phone": shop.get("public_contact") or ""},
        ))
    if not by_branch:
        return []
    # The branch with the most offers gives the most varied orders.
    return max(by_branch.values(), key=len)[:limit]


class Command(BaseCommand):
    help = "Development only: add ten fake orders in every status (never changes Marketplace stock)."

    def add_arguments(self, parser):
        parser.add_argument("--customer-id", type=int, default=DEFAULT_CUSTOMER_ID,
                            help="Accounts user ID the orders belong to, so they show in that customer's My orders.")
        parser.add_argument("--customer-name", default="Demo customer (fictional)")

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("seed_demo_orders is for local development only (DJANGO_DEBUG must be True).")

        customer_id = options["customer_id"]
        if customer_id <= 0:
            raise CommandError("--customer-id must be a positive number.")

        offers = marketplace_offers()
        if offers:
            self.stdout.write(f"Using offers from {offers[0].place['shop_name'] or 'a restaurant'} (Marketplace).")
        else:
            offers = FICTIONAL_OFFERS
            self.stdout.write("Marketplace lists no offers right now; using fictional ones.")

        now = timezone.now()
        created = 0
        for number, final, actor, reason, hours_ago, items in PLAN:
            key = f"{KEY_PREFIX}{number:02d}"
            if Order.objects.filter(customer_user_id=customer_id, request_key=key).exists():
                continue
            self._create(customer_id, options["customer_name"], key, final, actor, reason,
                         now - timedelta(hours=hours_ago), [(offers[i % len(offers)], qty) for i, qty in items], now)
            created += 1

        total = Order.objects.filter(customer_user_id=customer_id, request_key__startswith=KEY_PREFIX).count()
        self.stdout.write(self.style.SUCCESS(f"Added {created} fake order(s); this customer now has {total}."))

    def _create(self, customer_id, customer_name, key, final, actor, reason, placed, items, now):
        # An order has one line per offer: merge repeats of the same offer.
        merged = {}
        for offer, qty in items:
            previous = merged.get(offer.listing_id, (offer, 0))[1]
            merged[offer.listing_id] = (offer, previous + qty)
        first = next(iter(merged.values()))[0]
        # Reserved orders are still collectable: their window runs into the
        # evening. Finished ones had a window around when they were placed.
        start = placed - timedelta(minutes=30)
        end = max(placed, now) + timedelta(hours=3) if final == Status.CONFIRMED else placed + timedelta(hours=2)

        with transaction.atomic():
            order = create_order(
                customer_user_id=customer_id,
                customer_first_name=customer_name,
                customer_phone="+961 70 000 000",
                shop_id=first.shop_id,
                branch_id=first.branch_id,
                request_key=key,
                now=placed,
                place=first.place,
                lines=[{"listing_id": offer.listing_id, "quantity": qty, "title": offer.title,
                        "unit_price": offer.unit_price, "pickup_start": start, "pickup_end": end}
                       for offer, qty in merged.values()],
            )
            # Synced from the start: the timer job never calls Marketplace
            # to consume or release stock for these orders.
            order = change_status(order, Status.CONFIRMED, actor=Actor.SYSTEM, sync_state=SyncState.SYNCED,
                                  reason="Fake order (LP-77): no stock held", now=placed)
            if final != Status.CONFIRMED:
                finished = min(placed + timedelta(minutes=45), now)
                change_status(order, final, actor=actor, reason=reason, sync_state=SyncState.SYNCED, now=finished)
