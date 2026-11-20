"""LP-84: a restaurant limits how many of an offer one customer can reserve.

Marketplace sends the limit as max_per_customer on the offer (empty = no
limit). Checkout counts what the customer already reserved or collected from
the same offer in other orders: Reserved, being placed and Paid orders count;
Cancelled, Expired and Not reserved orders give their units back.
"""

import threading
import uuid

from django.db import connections
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient

from .models import Actor, Order, Status
from .orders import change_status
from .test_lp19_checkout import (
    CUSTOMER_ID,
    PUBLIC_PEM,
    TEST_KEY,
    URL,
    CheckoutTestCase,
    FakeMarketplace,
    token,
)

OTHER_CUSTOMER_ID = CUSTOMER_ID + 1


class LimitTestCase(CheckoutTestCase):

    def limit(self, listing_id, limit):
        self.market.offers[listing_id]["max_per_customer"] = limit

    def other_customer(self):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token(OTHER_CUSTOMER_ID)}")
        return client

    def assert_over_limit(self, response, listing_id=41, allowed=None, detail=None):
        self.assertEqual(response.status_code, 409, response.content)
        body = response.json()
        self.assertEqual(body["code"], "cart_changed")
        problem = next(problem for problem in body["problems"] if problem["code"] == "over_limit")
        self.assertEqual(problem["listing_id"], listing_id)
        if allowed is not None:
            self.assertEqual(problem["allowed"], allowed)
        if detail is not None:
            self.assertEqual(problem["detail"], detail)
        return body


class OneOrderTests(LimitTestCase):

    def test_an_offer_without_a_limit_has_none(self):
        self.assertEqual(self.reserve((41, 3)).status_code, 201)

    def test_up_to_the_limit_is_allowed(self):
        self.limit(41, 2)
        self.assertEqual(self.reserve((41, 2)).status_code, 201)

    def test_above_the_limit_is_refused_and_nothing_is_reserved(self):
        self.limit(41, 2)

        body = self.assert_over_limit(self.reserve((41, 3)), allowed=2, detail="Limit of 2 per customer.")

        self.assertEqual(body["problems"][0]["limit"], 2)
        self.assertFalse(Order.objects.exists())
        self.assertEqual(self.market.free(41), 3)
        self.assertNotIn("allocate", [call[0] for call in self.market.calls])

    def test_the_limit_belongs_to_one_offer(self):
        self.limit(41, 1)
        self.assertEqual(self.reserve((42, 4)).status_code, 201)

    def test_one_item_over_the_limit_refuses_the_whole_cart(self):
        self.limit(41, 1)

        body = self.assert_over_limit(self.reserve((41, 2), (42, 1)))

        self.assertEqual([problem["listing_id"] for problem in body["problems"]], [41])
        self.assertFalse(Order.objects.exists())
        self.assertEqual((self.market.free(41), self.market.free(42)), (3, 5))

    def test_not_enough_stock_is_reported_once_not_as_a_limit_too(self):
        self.limit(41, 2)
        self.market.offers[41]["total"] = 1

        body = self.client.post(URL, {"request_key": str(uuid.uuid4()), "lines": self.lines((41, 2))},
                                format="json").json()

        self.assertEqual([problem["code"] for problem in body["problems"]], ["only_n_left"])

    def test_a_limit_that_is_not_a_positive_whole_number_is_ignored(self):
        for value in (None, 0, -1, "2", True, 1.5):
            with self.subTest(value=value):
                self.limit(41, value)
                response = self.reserve((41, 3))
                self.assertEqual(response.status_code, 201)
                # Start the next round with the stock and the orders as they were.
                change_status(Order.objects.get(reservation_ref=response.json()["reservation_ref"]),
                              Status.CANCELLED, actor=Actor.CUSTOMER)
                self.market.allocations.clear()


class SeveralOrdersTests(LimitTestCase):

    def test_orders_add_up(self):
        self.limit(41, 2)
        self.assertEqual(self.reserve((41, 1)).status_code, 201)

        self.assert_over_limit(self.reserve((41, 2)), allowed=1,
                               detail="Limit of 2 per customer: you can reserve 1 more.")
        self.assertEqual(self.reserve((41, 1)).status_code, 201)
        self.assert_over_limit(self.reserve((41, 1)), allowed=0)

    def test_once_the_limit_is_used_up_no_more_can_be_reserved(self):
        self.limit(41, 2)
        self.assertEqual(self.reserve((41, 2)).status_code, 201)

        self.assert_over_limit(self.reserve((41, 1)), allowed=0,
                               detail="You have reached the limit of 2 per customer for this offer.")

    def test_a_cancelled_order_gives_its_units_back(self):
        self.limit(41, 2)
        order = Order.objects.get(reservation_ref=self.reserve((41, 2)).json()["reservation_ref"])
        change_status(order, Status.CANCELLED, actor=Actor.CUSTOMER)
        self.market.allocations.clear()  # the cancellation released the stock

        self.assertEqual(self.reserve((41, 2)).status_code, 201)

    def test_a_paid_order_still_counts(self):
        self.limit(41, 2)
        order = Order.objects.get(reservation_ref=self.reserve((41, 2)).json()["reservation_ref"])
        change_status(order, Status.COLLECTED, actor=Actor.SHOP)

        self.assert_over_limit(self.reserve((41, 1)), allowed=0)

    def test_an_expired_order_gives_its_units_back(self):
        self.limit(41, 2)
        order = Order.objects.get(reservation_ref=self.reserve((41, 2)).json()["reservation_ref"])
        change_status(order, Status.EXPIRED, actor=Actor.SYSTEM)
        self.market.allocations.clear()  # the expiry released the stock

        self.assertEqual(self.reserve((41, 2)).status_code, 201)

    def test_another_customers_orders_do_not_count(self):
        self.limit(41, 1)
        self.assertEqual(self.reserve((41, 1)).status_code, 201)

        self.assertEqual(self.reserve((41, 1), client=self.other_customer()).status_code, 201)


@override_settings(JWT_PUBLIC_KEY=PUBLIC_PEM, MARKETPLACE_SERVICE_KEY=TEST_KEY)
class SameMomentTests(TransactionTestCase):
    """Two tabs at the same moment, on PostgreSQL: the limit still holds."""

    def setUp(self):
        self.market = FakeMarketplace()
        patcher = self.market.patch()
        patcher.start()
        self.addCleanup(patcher.stop)
        self.market.offers[41]["max_per_customer"] = 1

    def test_two_checkouts_at_once_cannot_both_pass_the_limit(self):
        bodies = [{"request_key": str(uuid.uuid4()),
                   "lines": [{"listing_id": 41, "quantity": 1, "expected_unit_price": "1.50"}]}
                  for _ in range(2)]
        start = threading.Barrier(2)
        statuses, errors = [], []

        def press(body):
            try:
                client = APIClient()
                client.credentials(HTTP_AUTHORIZATION=f"Bearer {token()}")
                start.wait(timeout=30)
                statuses.append(client.post(URL, body, format="json").status_code)
            except Exception as error:  # noqa: BLE001 - reported below
                errors.append(repr(error))
            finally:
                connections.close_all()

        threads = [threading.Thread(target=press, args=(body,)) for body in bodies]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)

        self.assertEqual(errors, [])
        self.assertEqual(sorted(statuses), [201, 409])
        self.assertEqual(Order.objects.filter(status=Status.CONFIRMED).count(), 1)
        self.assertEqual(self.market.free(41), 2)
