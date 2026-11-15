"""LP-30: "same second" tests. Two actions at the same moment never break
stock or orders.

Each pair runs in real transactions on the real PostgreSQL test database
(TransactionTestCase), one thread per action, each thread with its own
database connection. A barrier releases both threads together, and every
pair is repeated several times so both orders of arrival are exercised.

After every race the timer job runs (LP-25), so whatever the race started
is finished, and then:
- the order has exactly one final status, with exactly one history row
  leaving Reserved;
- its stock changed exactly once in Marketplace (one move away from HELD:
  RELEASED, CONSUMED or SHOP_CANCELLED), so food is never returned twice.

Marketplace is LP-19's FakeMarketplace (one lock, allocations by reference,
moves only from HELD, like LP-20), extended to record every stock move. The
checkout x offer-taken-down pair runs against the real Marketplace in
services/marketplace (marketplace/test_lp30_same_second.py).
"""

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest import mock

from django.db import close_old_connections
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from . import marketplace_client, timers
from .cancellation import CancelRefused, cancel_by_customer, cancel_by_shop
from .checkout import item_ref
from .collection import CollectionRefused, pay_or_undo
from .models import Order, Status
from .test_lp19_checkout import TEST_KEY, PUBLIC_PEM, token
from .test_lp25_timers import DONE, TimerMarketplace

ROUNDS = 5
SHOP = {"id": 3, "branches": [{"id": 7, "name": "Hamra"}]}
OPERATOR_ID = 900
REFUSED = (CollectionRefused, CancelRefused)


class RaceMarketplace(TimerMarketplace):
    """Records every stock move (one per allocation and final state)."""

    def __init__(self):
        super().__init__()
        self.moves = []

    def _move(self, ref, to_status):
        allocation = self.allocations.get(ref)
        if allocation is not None and allocation["status"] == "HELD":
            allocation["status"] = to_status
            self.moves.append((ref, to_status))
            return True
        return False

    def stock_call(self, action, ref, listing_id):
        with self.lock:
            before = dict(self.allocations.get(ref) or {})
        answer = super().stock_call(action, ref, listing_id)
        with self.lock:
            after = self.allocations.get(ref) or {}
            if before.get("status") == "HELD" and after.get("status") != "HELD":
                self.moves.append((ref, after["status"]))
        return answer

    def release(self, ref, listing_id):  # LP-27 and checkout use this one
        return self.stock_call("release", ref, listing_id) == DONE

    def shop_cancel(self, ref, listing_id, actor_user_id, reason):  # LP-79
        self.calls.append(("shop_cancel", listing_id, str(ref)))
        with self.lock:
            allocation = self.allocations.get(ref)
            if allocation is None:
                return False
            if self._move(ref, "SHOP_CANCELLED"):
                self.offers[listing_id]["total"] -= allocation["quantity"]
            return allocation["status"] == "SHOP_CANCELLED"

    def patch(self):
        return mock.patch.multiple(marketplace_client, read_offer=self.read_offer, allocate=self.allocate,
                                   release=self.release, shop_cancel=self.shop_cancel,
                                   stock_call=self.stock_call)


def customer_client(user_id):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token(user_id=user_id)}")
    return client


@override_settings(JWT_PUBLIC_KEY=PUBLIC_PEM, MARKETPLACE_SERVICE_KEY=TEST_KEY)
class SameSecondTestCase(TransactionTestCase):
    def setUp(self):
        self.market = RaceMarketplace()
        self.market.offers[43]["total"] = 50  # enough for every round
        patcher = self.market.patch()
        patcher.start()
        self.addCleanup(patcher.stop)

    def place(self, listing_id=43, quantity=1, user_id=501):
        price = self.market.offers[listing_id]["discounted_price"]
        response = customer_client(user_id).post("/api/reservations", {
            "request_key": str(uuid.uuid4()),
            "lines": [{"listing_id": listing_id, "quantity": quantity, "expected_unit_price": price}]},
            format="json")
        self.assertEqual(response.status_code, 201, response.content)
        return Order.objects.get(reservation_ref=response.json()["reservation_ref"])

    def race(self, *actions):
        """Run the actions at the same moment, each on its own connection.
        Returns what each returned, or None if it was refused."""
        barrier = threading.Barrier(len(actions))

        def run(action):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return action()
            except REFUSED:
                return None
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=len(actions)) as pool:
            return list(pool.map(run, actions))

    def finish(self):
        """Let the timer job complete whatever the race started."""
        timers.run_timers(now=timezone.now() + timedelta(days=1))

    def assert_one_outcome(self, order, allowed):
        """One final status, one history row leaving Reserved, one stock move."""
        self.finish()
        order.refresh_from_db()
        self.assertIn(order.status, allowed)
        self.assertEqual(order.events.filter(from_status=Status.CONFIRMED).count(), 1)
        moves = [to for ref, to in self.market.moves if ref == item_ref(order, 43)]
        expected = {Status.CANCELLED: "SHOP_CANCELLED" if order.cancelled_by == "SHOP" else "RELEASED",
                    Status.COLLECTED: "CONSUMED", Status.EXPIRED: "RELEASED"}[order.status]
        self.assertEqual(moves, [expected])
        return order.status


class TwentyPeopleLastItemTests(SameSecondTestCase):
    """20 people reserving the last item: exactly one gets it."""

    def test_twenty_people_reserve_the_last_item_at_once(self):
        self.market.add(45, "Last loaf", "2.00", 1, start=timezone.now() - timedelta(minutes=5))
        clients = [customer_client(600 + n) for n in range(20)]

        def reserve(client):
            return lambda: client.post("/api/reservations", {"request_key": str(uuid.uuid4()), "lines": [
                {"listing_id": 45, "quantity": 1, "expected_unit_price": "2.00"}]}, format="json").status_code

        codes = self.race(*[reserve(c) for c in clients])
        self.assertEqual(codes.count(201), 1, codes)
        self.assertEqual(codes.count(409), 19, codes)
        self.assertEqual(Order.objects.filter(status=Status.CONFIRMED).count(), 1)
        self.assertFalse(Order.objects.filter(status=Status.PROCESSING).exists())
        self.assertEqual(self.market.free(45), 0)
        held = [a for a in self.market.allocations.values() if a["listing_id"] == 45 and a["status"] == "HELD"]
        self.assertEqual(len(held), 1)


class CustomerCancelVersusPaidTests(SameSecondTestCase):
    """Customer cancel x staff marking Paid."""

    def test_cancel_and_paid_at_the_same_moment(self):
        seen = set()
        for _ in range(ROUNDS):
            order = self.place()
            self.race(lambda: cancel_by_customer(order.reservation_ref, 501),
                      lambda: pay_or_undo(order.reservation_ref, 3, 7, 0))
            seen.add(self.assert_one_outcome(order, {Status.CANCELLED, Status.COLLECTED}))
        self.assertTrue(seen)


class PaidVersusExpiryTests(SameSecondTestCase):
    """Staff marking Paid x the deadline passing."""

    def test_paid_and_expiry_at_the_same_moment(self):
        for _ in range(ROUNDS):
            order = self.place()
            self.race(lambda: pay_or_undo(order.reservation_ref, 3, 7, 0),
                      lambda: timers.expire_due_orders(now=order.pick_up_by))
            self.assert_one_outcome(order, {Status.COLLECTED, Status.EXPIRED})

    def test_an_expired_order_never_returns_the_food_twice(self):
        order = self.place(quantity=2)
        self.race(*[lambda: timers.expire_due_orders(now=order.pick_up_by)] * 3,
                  timers.retry_pending_releases)
        self.assertEqual(self.assert_one_outcome(order, {Status.EXPIRED}), Status.EXPIRED)
        self.assertEqual(self.market.units_returned, 2)


class RestaurantCancelVersusPaidTests(SameSecondTestCase):
    """Restaurant cancel x staff marking Paid."""

    def test_shop_cancel_and_paid_at_the_same_moment(self):
        for _ in range(ROUNDS):
            order = self.place()
            total = self.market.offers[43]["total"]
            self.race(lambda: cancel_by_shop(order.reservation_ref, SHOP, OPERATOR_ID, "Food ran out"),
                      lambda: pay_or_undo(order.reservation_ref, 3, 7, 0))
            status = self.assert_one_outcome(order, {Status.CANCELLED, Status.COLLECTED})
            # A shop cancellation takes the unit out of stock exactly once.
            self.assertEqual(self.market.offers[43]["total"], total - (1 if status == Status.CANCELLED else 0))


class CustomerCancelVersusExpiryTests(SameSecondTestCase):
    """Customer cancel x the deadline passing: one of them wins, food back once."""

    def test_cancel_and_expiry_at_the_same_moment(self):
        for _ in range(ROUNDS):
            order = self.place()
            self.race(lambda: cancel_by_customer(order.reservation_ref, 501,
                                                 now=order.pick_up_by - timedelta(microseconds=1)),
                      lambda: timers.expire_due_orders(now=order.pick_up_by))
            self.assert_one_outcome(order, {Status.CANCELLED, Status.EXPIRED})
