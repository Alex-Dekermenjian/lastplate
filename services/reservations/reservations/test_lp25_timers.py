"""LP-25: the timer job. Late orders expire and the food goes back on sale.

One test class per "Done when" line of the Jira card, plus its "Tests to
write" list. Every rule is checked with a fake clock (an explicit `now`, or a
patched timezone.now). Marketplace is LP-19's FakeMarketplace, extended with
the release and consume calls the timer makes.
"""

import io
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.core.management import call_command
from django.db import close_old_connections
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from . import marketplace_client, timers
from .checkout import item_ref
from .collection import UNDO_WINDOW, CollectionRefused, can_undo, pay_or_undo
from .models import Actor, Order, Status, SyncState
from .orders import change_status
from .test_lp19_checkout import TEST_KEY, CheckoutTestCase, FakeMarketplace, PUBLIC_PEM, token

DONE, UNKNOWN, FAULT = marketplace_client.DONE, marketplace_client.UNKNOWN, marketplace_client.FAULT
SHOP = {"id": 3, "branches": [{"id": 7, "name": "Hamra"}]}


class TimerMarketplace(FakeMarketplace):
    """FakeMarketplace plus LP-25's stock_call: release and consume, moving
    an allocation only from HELD, exactly like LP-20."""

    def __init__(self):
        super().__init__()
        self.consume_down = False
        self.units_returned = 0
        # Pickup already open, so the restaurant can mark it Paid now.
        self.add(43, "Manakish", "3.00", 4, start=timezone.now() - timedelta(minutes=30))

    def stock_call(self, action, ref, listing_id):
        self.calls.append((action, listing_id, str(ref)))
        if (action == "release" and self.release_down) or (action == "consume" and self.consume_down):
            return UNKNOWN
        with self.lock:
            if action == "release":
                allocation = self.allocations.setdefault(ref, {"listing_id": listing_id, "quantity": 0,
                                                               "status": "RELEASED"})
                if allocation["status"] == "HELD":
                    allocation["status"] = "RELEASED"
                    self.units_returned += allocation["quantity"]
                return DONE if allocation["status"] in ("RELEASED", "REJECTED") else FAULT
            allocation = self.allocations.get(ref)
            if allocation is None:
                return FAULT
            if allocation["status"] == "HELD":
                allocation["status"] = "CONSUMED"
            return DONE if allocation["status"] == "CONSUMED" else FAULT

    def status_of(self, order, listing_id):
        return self.allocations[item_ref(order, listing_id)]["status"]

    def count(self, action):
        return sum(1 for call in self.calls if call[0] == action)

    def patch(self):
        return mock.patch.multiple(marketplace_client, read_offer=self.read_offer, allocate=self.allocate,
                                   release=self.release, stock_call=self.stock_call)


class TimerTestCase(CheckoutTestCase):
    def setUp(self):
        super().setUp()
        self.market = TimerMarketplace()
        patcher = self.market.patch()
        patcher.start()
        self.addCleanup(patcher.stop)
        cache.clear()

    def place(self, *items):
        response = self.reserve(*(items or ((41, 1),)))
        self.assertIn(response.status_code, (201, 202), response.content)
        return Order.objects.get(reservation_ref=response.json().get("reservation_ref")) \
            if response.status_code == 201 else Order.objects.latest("id")


class LateOrdersExpireTests(TimerTestCase):
    """Done when: an order still Reserved after its "pick up by" time becomes
    Expired and its code stops working. If the offer is still open, the items
    go straight back on sale (2 left -> 3)."""

    def test_fake_clock_past_deadline_expires_and_stock_returns_once(self):
        order = self.place((41, 1))
        self.assertEqual(self.market.free(41), 2)
        self.assertEqual(timers.expire_due_orders(now=order.pick_up_by - timedelta(seconds=1)), [])
        expired = timers.expire_due_orders(now=order.pick_up_by)
        order.refresh_from_db()
        self.assertEqual([o.pk for o in expired], [order.pk])
        self.assertEqual((order.status, order.sync_state), (Status.EXPIRED, SyncState.SYNCED))
        self.assertEqual(self.market.free(41), 3)  # 2 left -> 3
        event = order.events.get(to_status=Status.EXPIRED)
        self.assertEqual((event.from_status, event.actor), (Status.CONFIRMED, Actor.SYSTEM))
        self.assertEqual(event.created_at, order.pick_up_by)

    def test_every_item_is_released(self):
        order = self.place((41, 1), (42, 2))
        timers.expire_due_orders(now=order.pick_up_by)
        self.assertEqual((self.market.free(41), self.market.free(42)), (3, 5))

    def test_the_expired_code_stops_working(self):
        order = self.place((43, 1))
        timers.expire_due_orders(now=order.pick_up_by)
        with self.assertRaises(CollectionRefused) as refused:
            pay_or_undo(order.reservation_ref, 3, 7, 0)
        self.assertEqual(refused.exception.body["code"], "pickup_expired")

    def test_only_reserved_orders_expire_never_paid_ones(self):
        paid = self.place((43, 1))
        paid = pay_or_undo(paid.reservation_ref, 3, 7, 0)
        late = timezone.now() + timedelta(days=1)
        self.assertEqual(timers.expire_due_orders(now=late), [])
        paid.refresh_from_db()
        self.assertEqual(paid.status, Status.COLLECTED)
        self.assertEqual(self.market.count("release"), 0)


class ClosingTimeTests(TimerTestCase):
    """Done when: if the deadline was the closing time, nothing goes back: the
    offer is over and the items count as unsold. At closing time the whole
    offer ends and disappears, whatever stock is left."""

    def test_deadline_at_closing_time_releases_but_the_offer_is_gone(self):
        start = timezone.now() - timedelta(minutes=10)
        self.market.add(44, "Late bread", "1.00", 5, start=start)
        # A 20-minute window, shorter than the one-hour hold.
        self.market.offers[44]["pickup_end"] = (start + timedelta(minutes=30)).isoformat()
        order = self.place((44, 1))
        self.assertEqual(order.pick_up_by, start + timedelta(minutes=30))
        with mock.patch("django.utils.timezone.now", return_value=order.pick_up_by + timedelta(seconds=1)):
            timers.expire_due_orders()
            # Marketplace no longer lists an ended offer, whatever is left.
            self.assertEqual(self.market.read_offer(44), (404, None))
        order.refresh_from_db()
        self.assertEqual(order.status, Status.EXPIRED)
        # Released all the same (plan §10.8): the units count as unsold.
        self.assertEqual(self.market.status_of(order, 44), "RELEASED")


class ExpiryOnEveryReadTests(TimerTestCase):
    """Done when: expiry never depends only on the timer: whenever an order or
    the offers list is opened, late orders expire on the spot."""

    def later(self, order):
        return mock.patch("reservations.timers.timezone.now", return_value=order.pick_up_by + timedelta(minutes=1))

    def test_opening_the_order_page_expires_it(self):
        order = self.place()
        with self.later(order):
            body = self.client.get(f"/api/reservations/{order.reservation_ref}").json()
        self.assertEqual(body["status_label"], "Expired")
        self.assertEqual(self.market.free(41), 3)

    def test_opening_my_orders_expires_it(self):
        order = self.place()
        with self.later(order):
            body = self.client.get("/api/reservations").json()
        self.assertEqual(body["orders"][0]["status"], Status.EXPIRED)

    def test_ordering_again_first_expires_late_orders(self):
        first, second = self.place(), self.place()
        # Two Reserved orders is the limit, unless they are late.
        self.assertEqual(self.reserve().json()["code"], "too_many_orders")
        with self.later(second):
            self.assertEqual(self.reserve((42, 1)).status_code, 201)
        first.refresh_from_db()
        self.assertEqual(first.status, Status.EXPIRED)

    def test_restaurant_lists_and_pickup_expire_it(self):
        order = self.place((43, 1))
        shop_client = APIClient()
        shop_client.credentials(HTTP_AUTHORIZATION="Bearer " + token(user_id=900, role="SHOP_OPERATOR"))
        with mock.patch("reservations.shop_views.operator_shop", return_value=(200, SHOP)), \
                mock.patch("reservations.collection_views.operator_shop", return_value=(200, SHOP)), \
                self.later(order):
            listed = shop_client.get("/api/shop/reservations").json()
            looked_up = shop_client.post("/api/shop/reservations/lookup",
                                         {"branch_id": 7, "pickup_code": order.pickup_code}, format="json")
        order.refresh_from_db()
        self.assertEqual(order.status, Status.EXPIRED)
        self.assertEqual(listed["orders"], [])
        self.assertEqual((looked_up.status_code, looked_up.json()["code"]), (409, "pickup_expired"))

    def test_the_offers_list_endpoint_needs_no_sign_in_and_runs_at_most_every_few_seconds(self):
        order = self.place()
        anonymous = APIClient()
        with self.later(order):
            first = anonymous.post("/api/timers/expire")
            second = anonymous.post("/api/timers/expire")
        self.assertEqual(first.json(), {"ran": True, "expired": 1})
        self.assertEqual(second.json(), {"ran": False, "expired": 0})
        self.assertEqual(anonymous.get("/api/timers/expire").status_code, 405)

    def test_a_broken_expiry_never_breaks_the_page(self):
        order = self.place()
        with mock.patch.object(timers, "expire_due_orders", side_effect=RuntimeError("boom")), \
                self.assertLogs("reservations.timers", "ERROR"):
            response = self.client.get(f"/api/reservations/{order.reservation_ref}")
        self.assertEqual(response.status_code, 200)


class RunTwiceTests(TimerTestCase):
    """Done when: running it twice does nothing extra (the food is returned
    only once); if it was off, the next run catches up."""

    def test_running_twice_changes_nothing(self):
        order = self.place()
        moment = order.pick_up_by + timedelta(minutes=1)
        first, second = timers.run_timers(now=moment), timers.run_timers(now=moment)
        self.assertEqual((first["expired"], second["expired"]), (1, 0))
        self.assertEqual(order.events.filter(to_status=Status.EXPIRED).count(), 1)
        self.assertEqual(self.market.units_returned, 1)
        self.assertEqual(self.market.free(41), 3)

    def test_a_run_after_being_off_catches_up(self):
        orders = [self.place((41, 1)), self.place((42, 2))]
        timers.run_timers(now=timezone.now() + timedelta(days=2))
        self.assertEqual({o.status for o in Order.objects.filter(pk__in=[o.pk for o in orders])}, {Status.EXPIRED})
        self.assertEqual((self.market.free(41), self.market.free(42)), (3, 5))

    def test_a_release_without_answer_is_retried_until_it_is_done(self):
        order = self.place()
        self.market.release_down = True
        timers.run_timers(now=order.pick_up_by)
        order.refresh_from_db()
        self.assertEqual((order.status, order.sync_state), (Status.EXPIRED, SyncState.RELEASE_PENDING))
        self.assertEqual(self.market.free(41), 2)
        self.market.release_down = False
        timers.run_timers(now=order.pick_up_by)
        timers.run_timers(now=order.pick_up_by)
        order.refresh_from_db()
        self.assertEqual(order.sync_state, SyncState.SYNCED)
        self.assertEqual((self.market.free(41), self.market.units_returned), (3, 1))

    def test_releases_left_pending_by_other_actions_are_retried(self):
        order = self.place()
        change_status(order, Status.CANCELLED, actor=Actor.CUSTOMER, sync_state=SyncState.RELEASE_PENDING)
        self.assertEqual(timers.retry_pending_releases(), 1)
        order.refresh_from_db()
        self.assertEqual((order.sync_state, self.market.free(41)), (SyncState.SYNCED, 3))

    def test_an_impossible_release_answer_needs_attention(self):
        order = self.place()
        self.market.allocations[item_ref(order, 41)]["status"] = "CONSUMED"
        timers.expire_due_orders(now=order.pick_up_by)
        order.refresh_from_db()
        self.assertEqual(order.sync_state, SyncState.NEEDS_ATTENTION)

    def test_one_failing_step_does_not_stop_the_others(self):
        order = self.place()
        with mock.patch.object(timers, "consume_paid_orders", side_effect=RuntimeError("down")), \
                self.assertLogs("reservations.timers", "ERROR"):
            counts = timers.run_timers(now=order.pick_up_by)
        self.assertEqual((counts["expired"], counts["consumed"]), (1, None))

    def test_the_command_runs_once(self):
        order = self.place()
        out = io.StringIO()
        with mock.patch("reservations.timers.timezone.now", return_value=order.pick_up_by):
            call_command("run_timers", stdout=out)
        self.assertIn("expired=1", out.getvalue())
        order.refresh_from_db()
        self.assertEqual(order.status, Status.EXPIRED)


class StuckProcessingTests(TimerTestCase):
    """Done when: orders stuck halfway between the two services for more than
    2 minutes are cleaned up and their stock released."""

    def test_stuck_processing_order_is_resolved_and_its_stock_released(self):
        self.market.allocate_down = lambda listing_id: listing_id == 42
        response = self.reserve((41, 1), (42, 1))
        self.assertEqual(response.status_code, 202)
        order = Order.objects.get()
        self.assertEqual((order.status, self.market.free(41)), (Status.PROCESSING, 2))
        self.assertEqual(timers.resolve_stuck_orders(now=order.created_at + timedelta(minutes=1)), [])
        resolved = timers.resolve_stuck_orders(now=order.created_at + timers.STUCK_AFTER)
        order.refresh_from_db()
        self.assertEqual([o.pk for o in resolved], [order.pk])
        self.assertEqual((order.status, order.sync_state), (Status.NOT_RESERVED, SyncState.SYNCED))
        self.assertEqual(self.market.free(41), 3)
        # The never-answered item now has a release tombstone.
        self.assertEqual(self.market.status_of(order, 42), "RELEASED")

    def test_a_late_retry_cannot_hold_stock_again(self):
        self.market.allocate_down = True
        key = str(uuid.uuid4())
        self.assertEqual(self.reserve((41, 1), key=key).status_code, 202)
        order = Order.objects.get()
        timers.resolve_stuck_orders(now=order.created_at + timers.STUCK_AFTER)
        self.market.allocate_down = False
        response = self.reserve((41, 1), key=key)
        self.assertEqual((response.status_code, response.json()["code"]), (409, "not_reserved"))
        self.assertEqual(self.market.free(41), 3)
        self.assertFalse(Order.objects.visible_to_users().exists())

    def test_orders_needing_staff_attention_are_left_alone(self):
        self.market.fault = True
        self.reserve()
        order = Order.objects.get()
        self.assertEqual(order.sync_state, SyncState.NEEDS_ATTENTION)
        self.assertEqual(timers.resolve_stuck_orders(now=timezone.now() + timedelta(hours=1)), [])


@override_settings(JWT_PUBLIC_KEY=PUBLIC_PEM, MARKETPLACE_SERVICE_KEY=TEST_KEY)
class PaidConsumptionTests(TransactionTestCase):
    """The same job consumes orders Paid more than 10 minutes ago (LP-24
    contract: claim first, then consume every line, then SYNCED)."""

    def setUp(self):
        self.market = TimerMarketplace()
        patcher = self.market.patch()
        patcher.start()
        self.addCleanup(patcher.stop)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token()}")
        response = client.post("/api/reservations", {"request_key": str(uuid.uuid4()), "lines": [
            {"listing_id": 43, "quantity": 1, "expected_unit_price": "3.00"}]}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        self.order = pay_or_undo(response.json()["reservation_ref"], 3, 7, 0)

    def test_paid_orders_are_consumed_after_the_undo_window_only(self):
        self.assertEqual(timers.consume_paid_orders(now=self.order.collected_at + timedelta(minutes=9)), 0)
        self.order.refresh_from_db()
        self.assertIsNone(self.order.consumption_started_at)
        self.assertEqual(timers.consume_paid_orders(now=self.order.collected_at + UNDO_WINDOW), 1)
        self.order.refresh_from_db()
        self.assertEqual(self.order.sync_state, SyncState.SYNCED)
        self.assertIsNotNone(self.order.consumption_started_at)
        self.assertFalse(can_undo(self.order, self.order.collected_at + timedelta(minutes=5)))
        self.assertEqual(self.market.status_of(self.order, 43), "CONSUMED")
        self.assertEqual(self.market.free(43), 3)  # sold units never come back
        self.assertEqual(timers.consume_paid_orders(now=self.order.collected_at + UNDO_WINDOW), 0)

    def test_no_answer_keeps_the_claim_and_retries(self):
        self.market.consume_down = True
        moment = self.order.collected_at + UNDO_WINDOW
        timers.consume_paid_orders(now=moment)
        self.order.refresh_from_db()
        self.assertEqual(self.order.sync_state, SyncState.CONSUME_PENDING)
        self.assertIsNotNone(self.order.consumption_started_at)  # undo is now impossible
        self.market.consume_down = False
        self.assertEqual(timers.consume_paid_orders(now=moment), 1)

    def test_an_impossible_answer_needs_attention(self):
        self.market.allocations[item_ref(self.order, 43)]["status"] = "RELEASED"
        timers.consume_paid_orders(now=self.order.collected_at + UNDO_WINDOW)
        self.order.refresh_from_db()
        self.assertEqual(self.order.sync_state, SyncState.NEEDS_ATTENTION)


@override_settings(JWT_PUBLIC_KEY=PUBLIC_PEM, MARKETPLACE_SERVICE_KEY=TEST_KEY)
class TwoProcessesTests(TransactionTestCase):
    """Done when: safe to run from two processes at the same moment."""

    def setUp(self):
        self.market = TimerMarketplace()
        patcher = self.market.patch()
        patcher.start()
        self.addCleanup(patcher.stop)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token()}")
        response = client.post("/api/reservations", {"request_key": str(uuid.uuid4()), "lines": [
            {"listing_id": 41, "quantity": 2, "expected_unit_price": "1.50"}]}, format="json")
        self.order = Order.objects.get(reservation_ref=response.json()["reservation_ref"])

    def run_in_thread(self, moment):
        close_old_connections()
        try:
            return len(timers.expire_due_orders(now=moment))
        finally:
            close_old_connections()

    def test_two_timers_at_once_expire_once_and_return_the_food_once(self):
        moment = self.order.pick_up_by
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(self.run_in_thread, [moment] * 4))
        self.assertEqual(sum(results), 1)
        self.assertEqual(self.order.events.filter(to_status=Status.EXPIRED).count(), 1)
        self.assertEqual(self.market.units_returned, 2)
        self.assertEqual(self.market.free(41), 3)
