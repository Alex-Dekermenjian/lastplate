"""LP-19: checkout, placing an order from the cart.

One test class per "Done when" line of the Jira card, plus its "Tests to
write" list. Marketplace is replaced by FakeMarketplace, which follows the
LP-20 rules (stock, holds, releases, refusals) and can be made unreachable.
"""

import io
import json
import logging
import threading
import time
import uuid
from datetime import timedelta
from decimal import Decimal
from unittest import mock
from urllib.error import HTTPError, URLError

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.db import connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from . import checkout, marketplace_client
from .marketplace_client import Answer
from .models import Actor, Order, Status, SyncState
from .orders import change_status

_PRIVATE = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PRIVATE_PEM = _PRIVATE.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                     serialization.NoEncryption()).decode()
PUBLIC_PEM = _PRIVATE.public_key().public_bytes(serialization.Encoding.PEM,
                                                serialization.PublicFormat.SubjectPublicKeyInfo).decode()
TEST_KEY = "test-only-marketplace-key-" + "m" * 30
CUSTOMER_ID = 501
URL = "/api/reservations"


def token(user_id=CUSTOMER_ID, role="CUSTOMER", **claims):
    payload = {"user_id": user_id, "role": role, "token_type": "access",
               "exp": timezone.now() + timedelta(minutes=15), **claims}
    return jwt.encode(payload, PRIVATE_PEM, algorithm="RS256")


class FakeMarketplace:
    """Marketplace as LP-20 built it: one lock, stock, allocations by ref."""

    def __init__(self):
        self.lock = threading.Lock()
        self.offers, self.allocations = {}, {}
        self.read_down = self.allocate_down = self.release_down = False
        self.fault = False
        self.calls = []
        start = timezone.now() + timedelta(minutes=30)
        self.add(41, "Glazed donuts", "1.50", 3, start=start)
        self.add(42, "Croissant", "2.25", 5, start=start)

    def add(self, listing_id, title, price, total, start=None, shop_id=3, branch_id=7):
        start = start or timezone.now() + timedelta(minutes=30)
        self.offers[listing_id] = {
            "id": listing_id, "title": title, "discounted_price": price, "total": total,
            "pickup_start": start.isoformat(), "pickup_end": (start + timedelta(hours=3)).isoformat(),
            "shop": {"id": shop_id, "name": "Cedar Bakery"}, "branch": {"id": branch_id, "name": "Hamra"},
            "terms_revision": 1, "status": "PUBLISHED",
        }

    def free(self, listing_id):
        held = sum(a["quantity"] for a in self.allocations.values()
                   if a["listing_id"] == listing_id and a["status"] in ("HELD", "CONSUMED"))
        return self.offers[listing_id]["total"] - held

    def _ended(self, offer):
        return timezone.datetime.fromisoformat(offer["pickup_end"]) <= timezone.now()

    def read_offer(self, listing_id):
        self.calls.append(("read", listing_id))
        if self.read_down:
            raise marketplace_client.MarketplaceUnavailable("down")
        with self.lock:
            offer = self.offers.get(listing_id)
            if not offer or offer["status"] != "PUBLISHED" or self._ended(offer) or self.free(listing_id) <= 0:
                return 404, None
            return 200, {**offer, "free_quantity": self.free(listing_id)}

    def _answer(self, ref, allocation):
        offer = self.offers[allocation["listing_id"]]
        body = {"reservation_ref": str(ref), "listing_id": allocation["listing_id"],
                "quantity": allocation["quantity"], "status": allocation["status"],
                "reject_reason": allocation.get("reason") or None,
                "terms": {"unit_price": allocation.get("price", offer["discounted_price"]),
                          "pickup_start": offer["pickup_start"], "pickup_end": offer["pickup_end"]}}
        if allocation["status"] == "REJECTED":
            return Answer("REJECTED", body, allocation["reason"])
        return Answer(allocation["status"], body)

    def allocate(self, ref, listing_id, quantity, expected_terms_revision):
        self.calls.append(("allocate", listing_id, str(ref)))
        if self.fault:
            return Answer("FAULT", code="invalid_service_key")
        if self.allocate_down is True or (callable(self.allocate_down) and self.allocate_down(listing_id)):
            return Answer("UNKNOWN")
        with self.lock:
            if ref in self.allocations:
                return self._answer(ref, self.allocations[ref])
            offer = self.offers[listing_id]
            reason = ("not_published" if offer["status"] != "PUBLISHED" else
                      "pickup_ended" if self._ended(offer) else
                      "terms_changed" if offer["terms_revision"] != expected_terms_revision else
                      "not_enough_stock" if self.free(listing_id) < quantity else "")
            self.allocations[ref] = {"listing_id": listing_id, "quantity": quantity,
                                     "status": "REJECTED" if reason else "HELD", "reason": reason}
            return self._answer(ref, self.allocations[ref])

    def release(self, ref, listing_id):
        self.calls.append(("release", listing_id, str(ref)))
        if self.release_down:
            return False
        with self.lock:
            allocation = self.allocations.setdefault(ref, {"listing_id": listing_id, "quantity": 0,
                                                           "status": "RELEASED"})
            if allocation["status"] == "HELD":
                allocation["status"] = "RELEASED"
            return True

    def patch(self):
        return mock.patch.multiple(marketplace_client, read_offer=self.read_offer,
                                   allocate=self.allocate, release=self.release)


@override_settings(JWT_PUBLIC_KEY=PUBLIC_PEM, MARKETPLACE_SERVICE_KEY=TEST_KEY)
class CheckoutTestCase(TestCase):
    def setUp(self):
        self.market = FakeMarketplace()
        patcher = self.market.patch()
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token()}")

    def lines(self, *items):
        items = items or ((41, 1),)
        return [{"listing_id": listing_id, "quantity": quantity,
                 "expected_unit_price": self.market.offers[listing_id]["discounted_price"]}
                for listing_id, quantity in items]

    def reserve(self, *items, key=None, client=None):
        body = {"request_key": key or str(uuid.uuid4()), "lines": self.lines(*items)}
        return (client or self.client).post(URL, body, format="json")


class ReserveTests(CheckoutTestCase):
    """Done when: the customer taps Reserve; the server checks every item
    again, holds the stock, creates the order as Reserved with its "pick up
    by" time."""

    def test_happy_path_creates_a_reserved_order_and_stock_drops(self):
        response = self.reserve((41, 2), (42, 1))
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual((body["status"], body["status_label"]), ("CONFIRMED", "Reserved"))
        self.assertRegex(body["pickup_code"], r"^[A-HJ-NP-Z2-9]{6}$")
        self.assertEqual(body["total_cash"], "5.25")
        self.assertEqual((self.market.free(41), self.market.free(42)), (1, 4))

        order = Order.objects.get()
        self.assertEqual((order.customer_user_id, order.shop_id, order.branch_id), (CUSTOMER_ID, 3, 7))
        self.assertEqual((order.status, order.sync_state), (Status.CONFIRMED, SyncState.SYNCED))
        self.assertEqual(list(order.events.values_list("from_status", "to_status")),
                         [("", "PROCESSING"), ("PROCESSING", "CONFIRMED")])

    def test_pick_up_by_is_set_from_the_order_time_and_the_window(self):
        start = timezone.now() - timedelta(minutes=10)  # window already open
        self.market.add(50, "Bread", "1.00", 2, start=start)
        before = timezone.now()
        body = self.reserve((50, 1)).json()
        pick_up_by = timezone.datetime.fromisoformat(body["pick_up_by"])
        self.assertTrue(before + timedelta(minutes=59) <= pick_up_by <= timezone.now() + timedelta(minutes=61))

    def test_every_item_is_checked_again_and_held(self):
        self.reserve((41, 1), (42, 2))
        kinds = [call[0] for call in self.market.calls]
        self.assertEqual(kinds, ["read", "read", "allocate", "allocate"])

    def test_names_prices_and_windows_come_from_marketplace_not_the_browser(self):
        body = {"request_key": str(uuid.uuid4()),
                "lines": [{"listing_id": 41, "quantity": 1, "title": "Free cake", "unit_price": "0.01",
                           "expected_unit_price": "1.50"}]}
        self.assertEqual(self.client.post(URL, body, format="json").status_code, 201)
        line = Order.objects.get().lines.get()
        self.assertEqual((line.title, line.unit_price), ("Glazed donuts", Decimal("1.50")))

    def test_each_item_gets_its_own_reference_derived_from_the_order(self):
        self.reserve((41, 1), (42, 1))
        order = Order.objects.get()
        refs = [call[2] for call in self.market.calls if call[0] == "allocate"]
        self.assertEqual(refs, [str(checkout.item_ref(order, 41)), str(checkout.item_ref(order, 42))])
        self.assertNotEqual(refs[0], refs[1])
        self.assertEqual(checkout.item_ref(order, 41), uuid.uuid5(order.reservation_ref, "listing:41"))


class SomethingChangedTests(CheckoutTestCase):
    """Done when: if anything changed, no order is made and the cart shows
    what changed ("Only 1 left", "This offer was taken down", "Pickup ended")."""

    def assert_refused(self, response, code, detail=None, listing_id=None):
        self.assertEqual(response.status_code, 409, response.content)
        body = response.json()
        self.assertEqual(body["code"], "cart_changed")
        problem = body["problems"][0]
        self.assertEqual(problem["code"], code)
        if detail:
            self.assertEqual(problem["detail"], detail)
        if listing_id:
            self.assertEqual(problem["listing_id"], listing_id)
        self.assertFalse(Order.objects.visible_to_users().exists())
        return body

    def test_only_one_left(self):
        self.market.offers[41]["total"] = 1
        body = self.assert_refused(self.reserve((41, 2)), "only_n_left", "Only 1 left.", 41)
        self.assertEqual(body["problems"][0]["free_quantity"], 1)
        self.assertFalse(Order.objects.exists())  # found before anything was saved

    def test_offer_taken_down(self):
        self.market.offers[41]["status"] = "WITHDRAWN"
        self.assert_refused(self.reserve((41, 1)), "not_available", "This offer is no longer available.")

    def test_taken_down_between_the_check_and_the_hold(self):
        real_read = self.market.read_offer

        def read_then_withdraw(listing_id):
            answer = real_read(listing_id)
            self.market.offers[41]["status"] = "WITHDRAWN"
            return answer

        with mock.patch.object(marketplace_client, "read_offer", side_effect=read_then_withdraw):
            self.assert_refused(self.reserve((41, 1)), "taken_down", "This offer was taken down.")
        self.assertEqual(Order.objects.get().status, Status.NOT_RESERVED)

    def test_pickup_ended(self):
        self.market.add(51, "Late bread", "1.00", 3, start=timezone.now() - timedelta(hours=3, seconds=1))
        self.market.offers[51]["pickup_end"] = (timezone.now() + timedelta(seconds=1)).isoformat()
        real_read = self.market.read_offer

        def read_then_wait(listing_id):
            answer = real_read(listing_id)
            time.sleep(1.1)  # the window closes before the hold
            return answer

        with mock.patch.object(marketplace_client, "read_offer", side_effect=read_then_wait):
            self.assert_refused(self.reserve((51, 1)), "pickup_ended", "Pickup ended.")

    def test_price_changed(self):
        body = {"request_key": str(uuid.uuid4()),
                "lines": [{"listing_id": 41, "quantity": 1, "expected_unit_price": "1.00"}]}
        refused = self.assert_refused(self.client.post(URL, body, format="json"), "price_changed",
                                      "The price changed to $1.50.")
        self.assertEqual(refused["problems"][0]["unit_price"], "1.50")

    def test_one_line_out_of_stock_no_order_and_the_other_hold_is_released(self):
        real_read = self.market.read_offer

        def read_then_sell_out(listing_id):
            answer = real_read(listing_id)
            if listing_id == 42:  # someone else takes all croissants meanwhile
                self.market.allocations[uuid.uuid4()] = {"listing_id": 42, "quantity": 5, "status": "HELD"}
            return answer

        with mock.patch.object(marketplace_client, "read_offer", side_effect=read_then_sell_out):
            body = self.assert_refused(self.reserve((41, 2), (42, 1)), "only_n_left", listing_id=42)
        self.assertEqual(body["problems"][0]["detail"], "Sold out.")
        order = Order.objects.get()
        self.assertEqual((order.status, order.sync_state), (Status.NOT_RESERVED, SyncState.SYNCED))
        self.assertEqual(self.market.free(41), 3)  # the donuts went back
        self.assertIn(("release", 41, str(checkout.item_ref(order, 41))), self.market.calls)

    def test_a_release_without_answer_waits_for_the_lp25_job(self):
        self.market.release_down = True
        self.market.offers[42]["status"] = "WITHDRAWN"
        real_read = self.market.read_offer
        with mock.patch.object(marketplace_client, "read_offer",
                               side_effect=lambda i: real_read(i) if i == 41 else (200, {**self.market.offers[42], "status": "PUBLISHED", "free_quantity": 5})):
            self.assert_refused(self.reserve((41, 1), (42, 1)), "taken_down")
        order = Order.objects.get()
        self.assertEqual((order.status, order.sync_state), (Status.NOT_RESERVED, SyncState.RELEASE_PENDING))

    def test_items_from_two_branches_are_refused(self):
        self.market.add(60, "Salad", "6.00", 4, branch_id=8)
        self.assert_refused(self.reserve((41, 1), (60, 1)), "several_places")

    def test_terms_that_differ_from_the_check_are_released(self):
        real_allocate = self.market.allocate

        def allocate_other_price(ref, listing_id, quantity, revision):
            answer = real_allocate(ref, listing_id, quantity, revision)
            answer.allocation["terms"]["unit_price"] = "9.99"
            return answer

        with mock.patch.object(marketplace_client, "allocate", side_effect=allocate_other_price):
            self.assert_refused(self.reserve((41, 1)), "offer_changed")
        self.assertEqual(self.market.free(41), 3)


class DoubleClickAndOutageTests(CheckoutTestCase):
    """Done when: double click or lost internet still gives one order; if
    Marketplace is down the customer sees "Try again" and nothing is left
    half-saved."""

    def test_double_submit_gives_one_order(self):
        key = str(uuid.uuid4())
        first, second = self.reserve((41, 2), key=key), self.reserve((41, 2), key=key)
        self.assertEqual((first.status_code, second.status_code), (201, 200))
        self.assertEqual(first.json(), second.json())
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(self.market.free(41), 1)

    def test_same_key_for_another_cart_is_refused(self):
        key = str(uuid.uuid4())
        self.reserve((41, 1), key=key)
        response = self.reserve((41, 2), key=key)
        self.assertEqual((response.status_code, response.json()["code"]), (409, "request_key_reused"))

    def test_marketplace_down_before_saving_nothing_is_saved(self):
        self.market.read_down = True
        response = self.reserve((41, 1))
        self.assertEqual((response.status_code, response.json()["code"]), (503, "try_again"))
        self.assertIn("try again", response.json()["detail"].lower())
        self.assertFalse(Order.objects.exists())

    def test_marketplace_down_while_holding_no_reserved_order_and_a_retry_finishes_it(self):
        key = str(uuid.uuid4())
        self.market.allocate_down = True
        response = self.reserve((41, 1), key=key)
        self.assertEqual((response.status_code, response.json()["code"]), (202, "try_again"))
        self.assertIn("Reserve again", response.json()["detail"])
        order = Order.objects.get()
        self.assertEqual((order.status, order.sync_state), (Status.PROCESSING, SyncState.ALLOCATE_PENDING))
        self.assertFalse(Order.objects.visible_to_users().exists())  # hidden from the customer

        self.market.allocate_down = False
        retry = self.reserve((41, 1), key=key)
        self.assertEqual((retry.status_code, retry.json()["status_label"]), (200, "Reserved"))
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(self.market.free(41), 2)

    def test_lost_answer_after_the_first_item_was_held_then_retry(self):
        key = str(uuid.uuid4())
        self.market.allocate_down = lambda listing_id: listing_id == 42
        self.assertEqual(self.reserve((41, 1), (42, 1), key=key).status_code, 202)
        self.assertEqual(self.market.free(41), 2)  # held, as the plan says: not released on a timeout
        self.market.allocate_down = False
        self.assertEqual(self.reserve((41, 1), (42, 1), key=key).status_code, 200)
        self.assertEqual((self.market.free(41), self.market.free(42)), (2, 4))  # held once each

    def test_the_last_item_goes_to_one_order_only(self):
        key = str(uuid.uuid4())
        self.market.offers[41]["total"] = 1
        self.assertEqual(self.reserve((41, 1), key=key).status_code, 201)
        other = self.reserve((41, 1))
        self.assertEqual(other.status_code, 409)

    def test_a_contract_fault_is_flagged_for_staff(self):
        self.market.fault = True
        response = self.reserve((41, 1))
        self.assertEqual(response.status_code, 202)
        self.assertEqual(Order.objects.get().sync_state, SyncState.NEEDS_ATTENTION)
        self.assertFalse(Order.objects.visible_to_users().exists())


class WhoCanOrderTests(CheckoutTestCase):
    """Done when: only signed-in customers can order; at most 2 active
    orders; a blocked customer cannot order."""

    def test_signed_out_and_bad_tokens_are_refused(self):
        for credentials in ({}, {"HTTP_AUTHORIZATION": "Bearer not-a-token"},
                            {"HTTP_AUTHORIZATION": f"Bearer {token(token_type='refresh')}"}):
            client = APIClient()
            client.credentials(**credentials)
            self.assertEqual(self.reserve((41, 1), client=client).status_code, 401)
        self.assertFalse(Order.objects.exists())

    def test_restaurant_account_refused(self):
        for role in ("SHOP_OPERATOR", "ADMIN"):
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION=f"Bearer {token(user_id=7, role=role)}")
            self.assertEqual(self.reserve((41, 1), client=client).status_code, 403)
        self.assertFalse(Order.objects.exists())
        self.assertEqual(self.market.calls, [])

    def test_third_open_order_refused(self):
        self.assertEqual(self.reserve((41, 1)).status_code, 201)
        self.assertEqual(self.reserve((42, 1)).status_code, 201)
        third = self.reserve((42, 1))
        self.assertEqual((third.status_code, third.json()["code"]), (409, "too_many_orders"))
        self.assertEqual(Order.objects.count(), 2)

    def test_a_cancelled_order_frees_a_place(self):
        self.reserve((41, 1))
        self.reserve((42, 1))
        change_status(Order.objects.first(), Status.CANCELLED, actor=Actor.CUSTOMER)
        self.assertEqual(self.reserve((42, 1)).status_code, 201)

    def test_another_customers_orders_do_not_count(self):
        for _ in range(2):
            self.reserve((42, 1))
        other = APIClient()
        other.credentials(HTTP_AUTHORIZATION=f"Bearer {token(user_id=CUSTOMER_ID + 1)}")
        self.assertEqual(self.reserve((41, 1), client=other).status_code, 201)

    def test_a_blocked_customer_cannot_order(self):
        with mock.patch.object(checkout, "customer_is_blocked", return_value=True):
            response = self.reserve((41, 1))
        self.assertEqual((response.status_code, response.json()["code"]), (403, "customer_blocked"))
        self.assertFalse(Order.objects.exists())
        self.assertEqual(self.market.free(41), 3)

    def test_badly_formed_carts_are_refused(self):
        for body in ({}, {"request_key": "nope", "lines": self.lines()},
                     {"request_key": str(uuid.uuid4()), "lines": []},
                     {"request_key": str(uuid.uuid4()), "lines": [{"listing_id": 41, "quantity": 0}]},
                     {"request_key": str(uuid.uuid4()), "lines": [{"listing_id": 41, "quantity": True}]},
                     {"request_key": str(uuid.uuid4()), "lines": [{"listing_id": 41, "quantity": 1}] * 2}):
            response = self.client.post(URL, body, format="json")
            self.assertEqual((response.status_code, response.json()["code"]), (400, "invalid_request"), body)
        self.assertFalse(Order.objects.exists())


@override_settings(JWT_PUBLIC_KEY=PUBLIC_PEM, MARKETPLACE_SERVICE_KEY=TEST_KEY)
class SameMomentTests(TransactionTestCase):
    """Double clicks and two tabs at the same moment, on PostgreSQL."""

    def setUp(self):
        self.market = FakeMarketplace()
        patcher = self.market.patch()
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_at_once(self, bodies):
        start = threading.Barrier(len(bodies))
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
        return statuses

    def body(self, key, listing_id=41):
        return {"request_key": key, "lines": [{"listing_id": listing_id, "quantity": 1, "expected_unit_price":
                                               self.market.offers[listing_id]["discounted_price"]}]}

    def test_ten_clicks_at_once_give_one_order(self):
        key = str(uuid.uuid4())
        statuses = self.run_at_once([self.body(key)] * 10)
        self.assertEqual(sorted(statuses).count(201), 1)
        self.assertTrue(all(code in (200, 201) for code in statuses), statuses)
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(self.market.free(41), 2)

    def test_two_tabs_cannot_both_become_the_third_order(self):
        for listing_id in (41, 42):
            self.run_at_once([self.body(str(uuid.uuid4()), listing_id)])
        statuses = self.run_at_once([self.body(str(uuid.uuid4()), 42) for _ in range(5)])
        self.assertEqual(statuses, [409] * 5)
        self.assertEqual(Order.objects.filter(status=Status.CONFIRMED).count(), 2)

    def test_the_second_order_is_given_to_only_one_of_two_tabs_racing(self):
        self.run_at_once([self.body(str(uuid.uuid4()), 41)])
        statuses = self.run_at_once([self.body(str(uuid.uuid4()), 42) for _ in range(6)])
        self.assertEqual(sorted(statuses), [201] + [409] * 5)
        self.assertEqual(Order.objects.filter(status__in=checkout.ACTIVE_STATUSES).count(), 2)


class FakeResponse(io.BytesIO):
    def __init__(self, status, body):
        super().__init__(json.dumps(body).encode())
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@override_settings(MARKETPLACE_SERVICE_KEY=TEST_KEY, MARKETPLACE_SERVICE_URL="http://marketplace:8000")
class MarketplaceClientTests(TestCase):
    """The service key travels in the header, and answers are sorted per §6."""

    REF = uuid.uuid4()

    def call_allocate(self, outcome):
        sent = []

        def fake_urlopen(request, timeout):
            sent.append(request)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        with mock.patch.object(marketplace_client, "urlopen", side_effect=fake_urlopen):
            answer = marketplace_client.allocate(self.REF, 41, 2, 1)
        return answer, sent

    def held(self, status="HELD"):
        return {"reservation_ref": str(self.REF), "status": status, "quantity": 2, "listing_id": 41}

    def test_the_key_and_service_name_are_sent_to_the_internal_api(self):
        answer, sent = self.call_allocate(FakeResponse(201, self.held()))
        self.assertEqual(answer.kind, "HELD")
        request = sent[0]
        self.assertEqual(request.full_url, "http://marketplace:8000/internal/allocations")
        self.assertEqual(request.get_header("X-service-name"), "reservations")
        self.assertEqual(request.get_header("X-service-key"), TEST_KEY)
        self.assertEqual(json.loads(request.data)["reservation_ref"], str(self.REF))

    def test_the_public_offer_read_sends_no_key(self):
        sent = []
        with mock.patch.object(marketplace_client, "urlopen",
                               side_effect=lambda request, timeout: sent.append(request) or FakeResponse(200, {"id": 41})):
            marketplace_client.read_offer(41)
        self.assertIsNone(sent[0].get_header("X-service-key"))

    def test_answers_are_sorted_as_the_plan_says(self):
        rejected = {"code": "not_enough_stock", "allocation": {**self.held("REJECTED"),
                                                               "reject_reason": "not_enough_stock"}}
        cases = [
            (FakeResponse(200, self.held()), "HELD"),
            (HTTPError("u", 409, "x", {}, io.BytesIO(json.dumps(rejected).encode())), "REJECTED"),
            (HTTPError("u", 409, "x", {}, io.BytesIO(b'{"code": "ref_conflict"}')), "FAULT"),
            (HTTPError("u", 403, "x", {}, io.BytesIO(b'{"code": "invalid_service_key"}')), "FAULT"),
            (HTTPError("u", 503, "x", {}, io.BytesIO(b"")), "UNKNOWN"),
            (URLError("refused"), "UNKNOWN"),
            (TimeoutError(), "UNKNOWN"),
            (FakeResponse(201, {"reservation_ref": "someone-else"}), "FAULT"),
        ]
        for outcome, kind in cases:
            self.assertEqual(self.call_allocate(outcome)[0].kind, kind, outcome)

    @override_settings(MARKETPLACE_SERVICE_KEY="")
    def test_no_key_configured_is_a_fault_and_nothing_is_sent(self):
        with self.assertLogs("reservations.marketplace", level="ERROR"):
            answer, sent = self.call_allocate(FakeResponse(201, self.held()))
        self.assertEqual((answer.kind, sent), ("FAULT", []))

    def test_the_key_is_never_logged(self):
        with self.assertLogs("reservations.marketplace", level="ERROR") as logs:
            self.call_allocate(HTTPError("u", 403, "x", {}, io.BytesIO(b'{"code": "invalid_service_key"}')))
        self.assertNotIn(TEST_KEY, "\n".join(logs.output))

    def test_a_timeout_is_not_a_refusal(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        self.assertEqual(self.call_allocate(TimeoutError())[0].kind, "UNKNOWN")
