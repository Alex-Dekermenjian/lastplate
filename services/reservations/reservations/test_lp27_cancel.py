"""LP-27: the customer cancels their own order before its pick-up-by time.

Marketplace is LP-19's FakeMarketplace: stock, allocations by reference, and
an idempotent release, as LP-20 built it (test_lp20_stock_api covers the real
release twice changing stock once).
"""

import threading
import uuid
from datetime import timedelta

from django.db import connection
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .cancellation import CancelRefused, cancel_by_customer
from .checkout import customer_is_blocked, item_ref
from .models import Actor, CancelledBy, Order, Status, SyncState
from .orders import change_status
from .test_lp19_checkout import CUSTOMER_ID, PUBLIC_PEM, TEST_KEY, CheckoutTestCase, FakeMarketplace, token

OTHER_CUSTOMER = CUSTOMER_ID + 1


def cancel_url(order_or_ref):
    return f"/api/reservations/{getattr(order_or_ref, 'reservation_ref', order_or_ref)}/cancel"


class CancelTestCase(CheckoutTestCase):
    def place(self, *items):
        response = self.reserve(*(items or ((41, 2), (42, 1))))
        self.assertEqual(response.status_code, 201, response.content)
        return Order.objects.get(reservation_ref=response.json()["reservation_ref"])

    def cancel(self, order, client=None):
        return (client or self.client).post(cancel_url(order), format="json")

    def releases(self):
        return [call for call in self.market.calls if call[0] == "release"]


class CancelTests(CancelTestCase):
    def test_cancel_puts_the_food_back_on_sale(self):
        order = self.place()
        self.assertEqual((self.market.free(41), self.market.free(42)), (1, 4))

        response = self.cancel(order)

        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual((body["status"], body["status_label"], body["cancelled_by"]),
                         ("CANCELLED", "Cancelled", "Customer"))
        order.refresh_from_db()
        self.assertEqual((order.status, order.cancelled_by, order.sync_state),
                         (Status.CANCELLED, CancelledBy.CUSTOMER, SyncState.SYNCED))
        self.assertEqual((self.market.free(41), self.market.free(42)), (3, 5))
        # Each item's own allocation, the reference LP-19 held it with.
        self.assertCountEqual(self.releases(), [("release", 41, str(item_ref(order, 41))),
                                                ("release", 42, str(item_ref(order, 42)))])
        event = order.events.order_by("-id").first()
        self.assertEqual((event.from_status, event.to_status, event.actor),
                         (Status.CONFIRMED, Status.CANCELLED, Actor.CUSTOMER))

    def test_pressing_cancel_twice_returns_the_stock_once(self):
        order = self.place()
        self.assertEqual(self.cancel(order).status_code, 200)

        second = self.cancel(order)

        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.json()["code"], "already_cancelled")
        self.assertEqual(len(self.releases()), 2)  # one per item, from the first press only
        self.assertEqual((self.market.free(41), self.market.free(42)), (3, 5))
        self.assertEqual(order.events.filter(to_status=Status.CANCELLED).count(), 1)

    def test_cancel_with_marketplace_down_waits_for_the_retry_job(self):
        order = self.place()
        self.market.release_down = True

        response = self.cancel(order)

        # The customer's cancellation stands; the release is retried later (LP-25).
        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        self.assertEqual((order.status, order.sync_state), (Status.CANCELLED, SyncState.RELEASE_PENDING))
        # Both items were tried, not just the first.
        self.assertEqual(len(self.releases()), 2)

    def test_cancelling_frees_a_place_for_a_new_order(self):
        first = self.place((41, 1))
        self.place((42, 1))
        self.assertEqual(self.reserve((42, 1)).json()["code"], "too_many_orders")
        self.cancel(first)
        self.assertEqual(self.reserve((42, 1)).status_code, 201)

    def test_cancelling_is_never_a_strike(self):
        # Strikes (LP-80) count Expired orders; a cancelled order never expires.
        order = self.place()
        self.cancel(order)
        self.assertFalse(order.events.filter(to_status=Status.EXPIRED).exists())
        self.assertFalse(customer_is_blocked(CUSTOMER_ID))
        with self.assertRaises(Exception):
            change_status(order, Status.EXPIRED, actor=Actor.SYSTEM)
        order.refresh_from_db()
        self.assertEqual(order.status, Status.CANCELLED)


class CannotCancelTests(CancelTestCase):
    def assert_refused(self, order, code, http_status=409):
        before = order.status
        response = self.cancel(order)
        self.assertEqual(response.status_code, http_status, response.content)
        self.assertEqual(response.json()["code"], code)
        order.refresh_from_db()
        self.assertEqual(order.status, before)
        self.assertEqual(self.releases(), [])
        return response

    def test_cannot_cancel_a_paid_order(self):
        order = change_status(self.place(), Status.COLLECTED, actor=Actor.SHOP)
        self.assert_refused(order, "already_paid")
        self.assertEqual((self.market.free(41), self.market.free(42)), (1, 4))

    def test_cannot_cancel_an_expired_order(self):
        order = change_status(self.place(), Status.EXPIRED, actor=Actor.SYSTEM)
        self.assert_refused(order, "expired")

    def test_cannot_cancel_an_order_the_restaurant_cancelled(self):
        order = change_status(self.place(), Status.CANCELLED, actor=Actor.SHOP, reason="Sold out")
        self.assert_refused(order, "already_cancelled")
        self.assertEqual(order.cancelled_by, CancelledBy.SHOP)

    def test_cannot_cancel_someone_elses_order(self):
        order = self.place()
        other = APIClient()
        other.credentials(HTTP_AUTHORIZATION=f"Bearer {token(user_id=OTHER_CUSTOMER)}")
        # 404, as for reading it: a reference reveals nothing about other orders.
        response = self.cancel(order, client=other)
        self.assertEqual(response.status_code, 404)
        order.refresh_from_db()
        self.assertEqual(order.status, Status.CONFIRMED)
        self.assertEqual(self.releases(), [])
        self.assertEqual((self.market.free(41), self.market.free(42)), (1, 4))

    def test_cannot_cancel_an_unknown_order(self):
        self.assertEqual(self.client.post(cancel_url(uuid.uuid4())).status_code, 404)

    def test_cannot_cancel_after_pick_up_by(self):
        order = self.place()
        Order.objects.filter(pk=order.pk).update(shop_phone="+961 1 234 567",
                                                 pick_up_by=timezone.now() - timedelta(seconds=1))
        response = self.assert_refused(order, "pickup_time_passed")
        self.assertEqual(response.json()["shop_phone"], "+961 1 234 567")
        self.assertIn("+961 1 234 567", response.json()["detail"])

    def test_cannot_cancel_exactly_at_pick_up_by(self):
        order = self.place()
        with self.assertRaises(CancelRefused) as refused:
            cancel_by_customer(order.reservation_ref, CUSTOMER_ID, now=order.pick_up_by)
        self.assertEqual(refused.exception.code, "pickup_time_passed")
        cancelled = cancel_by_customer(order.reservation_ref, CUSTOMER_ID,
                                       now=order.pick_up_by - timedelta(seconds=1))
        self.assertEqual(cancelled.status, Status.CANCELLED)

    def test_an_order_still_being_placed_cannot_be_cancelled(self):
        self.market.allocate_down = True
        self.assertEqual(self.reserve((41, 1)).status_code, 202)
        hidden = Order.objects.get(status=Status.PROCESSING)
        self.assertEqual(self.cancel(hidden).status_code, 404)

    def test_restaurants_and_signed_out_users_cannot_cancel(self):
        order = self.place()
        operator = APIClient()
        operator.credentials(HTTP_AUTHORIZATION=f"Bearer {token(role='SHOP_OPERATOR')}")
        self.assertEqual(self.cancel(order, client=operator).status_code, 403)
        self.assertEqual(APIClient().post(cancel_url(order)).status_code, 401)
        self.assertEqual(self.client.get(cancel_url(order)).status_code, 405)
        order.refresh_from_db()
        self.assertEqual(order.status, Status.CONFIRMED)


@override_settings(JWT_PUBLIC_KEY=PUBLIC_PEM, MARKETPLACE_SERVICE_KEY=TEST_KEY)
class ConcurrentCancelTests(TransactionTestCase):
    """Two presses at the same moment (two tabs): the row lock lets one win."""

    def test_two_cancels_at_once_release_the_food_once(self):
        market = FakeMarketplace()
        with market.patch():
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION=f"Bearer {token()}")
            placed = client.post("/api/reservations", {
                "request_key": str(uuid.uuid4()),
                "lines": [{"listing_id": 41, "quantity": 2, "expected_unit_price": "1.50"}]}, format="json")
            self.assertEqual(placed.status_code, 201, placed.content)
            ref = placed.json()["reservation_ref"]

            barrier, answers = threading.Barrier(2), []

            def press():
                try:
                    barrier.wait()
                    caller = APIClient()
                    caller.credentials(HTTP_AUTHORIZATION=f"Bearer {token()}")
                    answers.append(caller.post(cancel_url(ref)).status_code)
                finally:
                    connection.close()

            threads = [threading.Thread(target=press) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

        order = Order.objects.get(reservation_ref=ref)
        self.assertEqual(sorted(answers), [200, 409])
        self.assertEqual([c for c in market.calls if c[0] == "release"],
                         [("release", 41, str(item_ref(order, 41)))])
        self.assertEqual(market.free(41), 3)
        self.assertEqual(order.events.filter(to_status=Status.CANCELLED).count(), 1)
