"""LP-77: seed_demo_orders fills the database with fake orders in every
status, only in development, without duplicates and without touching
Marketplace stock."""

from collections import Counter
from datetime import timedelta
from io import StringIO
from unittest import mock

from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from reservations import marketplace_client as market
from reservations.management.commands.seed_demo_orders import DEFAULT_CUSTOMER_ID, FICTIONAL_ID, PLAN
from reservations.models import CancelledBy, Order, OrderEvent, Status, SyncState
from reservations.timers import run_timers

LISTINGS = [
    {"id": 41, "title": "Croissant Box", "discounted_price": "4.50",
     "shop": {"id": 3, "name": "Cedar Bakery", "public_contact": "+961 1 234 567"},
     "branch": {"id": 7, "name": "Hamra", "address": "12 Bliss Street, Beirut"}},
    {"id": 42, "title": "Evening LastBag", "discounted_price": "5.00",
     "shop": {"id": 3, "name": "Cedar Bakery", "public_contact": "+961 1 234 567"},
     "branch": {"id": 7, "name": "Hamra", "address": "12 Bliss Street, Beirut"}},
]


def marketplace(listings=LISTINGS):
    """Marketplace answering only the public listings read."""

    def request(method, path, payload=None, internal=False):
        assert (method, path, internal) == ("GET", "/api/listings", False), (method, path)
        return 200, listings

    return mock.patch.object(market, "_request", side_effect=request)


@override_settings(DEBUG=True)
class SeedDemoOrdersTests(TestCase):
    def seed(self, **options):
        out = StringIO()
        call_command("seed_demo_orders", stdout=out, **options)
        return out.getvalue()

    def seeded(self, customer_id=DEFAULT_CUSTOMER_ID):
        return Order.objects.filter(customer_user_id=customer_id)

    def test_fills_every_status_with_items_and_history(self):
        with marketplace():
            self.seed()

        orders = self.seeded()
        self.assertEqual(orders.count(), len(PLAN))
        self.assertEqual(Counter(orders.values_list("status", flat=True)), {
            Status.CONFIRMED: 3, Status.COLLECTED: 3, Status.CANCELLED: 2, Status.EXPIRED: 2,
        })
        self.assertEqual(set(orders.filter(status=Status.CANCELLED).values_list("cancelled_by", flat=True)),
                         {CancelledBy.CUSTOMER, CancelledBy.SHOP})
        for order in orders:
            self.assertTrue(order.lines.exists())
            self.assertEqual(order.total_cash, sum(line.line_total for line in order.lines.all()))
            self.assertGreaterEqual(OrderEvent.objects.filter(order=order).count(), 2)
            self.assertEqual(order.sync_state, SyncState.SYNCED)

    def test_uses_real_offers_and_pick_up_by_around_now(self):
        with marketplace():
            self.seed()

        order = self.seeded().filter(status=Status.CONFIRMED).first()
        self.assertEqual((order.shop_id, order.branch_id, order.shop_name), (3, 7, "Cedar Bakery"))
        self.assertIn(order.lines.first().listing_id, (41, 42))
        now = timezone.now()
        for reserved in self.seeded().filter(status=Status.CONFIRMED):
            self.assertTrue(now < reserved.pick_up_by < now + timedelta(hours=2))

    def test_running_twice_gives_the_same_number_of_orders(self):
        with marketplace():
            self.seed()
            out = self.seed()

        self.assertEqual(self.seeded().count(), len(PLAN))
        self.assertIn("Added 0 fake order(s)", out)

    def test_another_customer_gets_their_own_set(self):
        with marketplace():
            self.seed()
            self.seed(customer_id=12, customer_name="Rami")

        self.assertEqual(self.seeded(12).count(), len(PLAN))
        self.assertEqual(self.seeded(12).first().customer_first_name, "Rami")

    def test_without_marketplace_offers_it_uses_fictional_ones(self):
        for patch in (marketplace([]),
                      mock.patch.object(market, "_request", side_effect=market.MarketplaceUnavailable("down"))):
            with self.subTest(patch=patch), patch:
                self.seed(customer_id=50 + len(self.seeded(50)))
        order = Order.objects.filter(customer_user_id=50).first()
        self.assertEqual(order.shop_id, FICTIONAL_ID)
        self.assertIn("fictional", order.lines.first().title)

    @override_settings(DEBUG=False)
    def test_refuses_when_debug_is_off(self):
        with marketplace(), self.assertRaisesMessage(CommandError, "local development only"):
            self.seed()
        self.assertFalse(Order.objects.exists())

    def test_never_holds_or_releases_stock(self):
        # marketplace() fails the test on any call but the public listings read.
        with marketplace():
            self.seed()
        with mock.patch.object(market, "stock_call") as stock_call, \
                mock.patch.object(market, "_request", side_effect=AssertionError("no Marketplace call")):
            run_timers()
        stock_call.assert_not_called()
        self.assertEqual(self.seeded().filter(status=Status.COLLECTED).count(), 3)
