"""LP-78: a customer's own orders: the list, and one order by its reference.

Same customer authentication as LP-19's checkout (an Accounts access token,
role CUSTOMER). Only Reserved, Paid, Cancelled and Expired orders are shown.
"""

import uuid
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from . import views
from .models import Actor, Order, Status
from .orders import change_status, create_order
from .test_lp19_checkout import CUSTOMER_ID, PUBLIC_PEM, CheckoutTestCase, token

OTHER_CUSTOMER = CUSTOMER_ID + 1
LIST_URL = "/api/reservations"


def detail_url(order_or_ref):
    return f"/api/reservations/{getattr(order_or_ref, 'reservation_ref', order_or_ref)}"


def make_order(customer=CUSTOMER_ID, status=Status.CONFIRMED, title="Glazed donuts", minutes_ago=0, **change):
    start = timezone.now() + timedelta(hours=1)
    order = create_order(
        customer_user_id=customer, shop_id=3, branch_id=7, request_key=str(uuid.uuid4()),
        now=timezone.now() - timedelta(minutes=minutes_ago),
        lines=[{"listing_id": 41, "quantity": 2, "title": title, "unit_price": Decimal("1.50"),
                "pickup_start": start, "pickup_end": start + timedelta(hours=3)}],
    )
    if status == Status.PROCESSING:
        return order
    if status == Status.NOT_RESERVED:
        return change_status(order, Status.NOT_RESERVED, actor=Actor.SYSTEM)
    order = change_status(order, Status.CONFIRMED, actor=Actor.SYSTEM)
    if status != Status.CONFIRMED:
        order = change_status(order, status, **change)
    return order


@override_settings(JWT_PUBLIC_KEY=PUBLIC_PEM)
class MyOrdersApiTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token()}")

    def as_user(self, user_id=CUSTOMER_ID, role="CUSTOMER"):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token(user_id=user_id, role=role)}")
        return client

    def listed_refs(self, client=None):
        response = (client or self.client).get(LIST_URL)
        self.assertEqual(response.status_code, 200, response.content)
        return [o["reservation_ref"] for o in response.json()["orders"]]


class ListTests(MyOrdersApiTestCase):
    def test_the_list_shows_only_your_own_orders(self):
        mine = make_order()
        theirs = make_order(customer=OTHER_CUSTOMER)
        self.assertEqual(self.listed_refs(), [str(mine.reservation_ref)])
        self.assertEqual(self.listed_refs(self.as_user(OTHER_CUSTOMER)), [str(theirs.reservation_ref)])

    def test_newest_first_and_at_most_50(self):
        old = make_order(minutes_ago=30)
        new = make_order(minutes_ago=1)
        self.assertEqual(self.listed_refs(), [str(new.reservation_ref), str(old.reservation_ref)])
        with mock.patch("reservations.views.MAX_LISTED", 1):
            self.assertEqual(self.listed_refs(), [str(new.reservation_ref)])
        self.assertEqual(views.MAX_LISTED, 50)

    def test_each_order_has_what_the_pages_show(self):
        order = make_order()
        row = self.client.get(LIST_URL).json()["orders"][0]
        self.assertEqual((row["status_label"], row["pickup_code"], row["total_cash"]),
                         ("Reserved", order.pickup_code, "3.00"))
        self.assertEqual(row["pick_up_by"], order.pick_up_by.isoformat())
        self.assertEqual([(line["title"], line["quantity"], line["unit_price"]) for line in row["lines"]],
                         [("Glazed donuts", 2, "1.50")])
        for field in ("shop_id", "branch_id", "created_at", "cancelled_by", "collected_at"):
            self.assertIn(field, row)

    def test_an_empty_list(self):
        self.assertEqual(self.client.get(LIST_URL).json(), {"orders": []})


class NeverShownTests(MyOrdersApiTestCase):
    def test_processing_and_not_reserved_are_never_shown(self):
        processing = make_order(status=Status.PROCESSING)
        refused = make_order(status=Status.NOT_RESERVED)
        shown = make_order()
        self.assertEqual(self.listed_refs(), [str(shown.reservation_ref)])
        for hidden in (processing, refused):
            self.assertEqual(self.client.get(detail_url(hidden)).status_code, 404)


class SingleOrderTests(MyOrdersApiTestCase):
    def test_the_customer_opens_their_own_order(self):
        order = make_order()
        response = self.client.get(detail_url(order))
        self.assertEqual(response.status_code, 200)
        self.assertEqual((response.json()["reservation_ref"], response.json()["pickup_code"]),
                         (str(order.reservation_ref), order.pickup_code))

    def test_customer_a_cannot_open_customer_bs_order(self):
        theirs = make_order(customer=OTHER_CUSTOMER)
        response = self.client.get(detail_url(theirs))
        self.assertEqual(response.status_code, 404)  # 404, never 403
        self.assertEqual(response.json()["code"], "not_found")
        self.assertNotIn(theirs.pickup_code, response.content.decode())

    def test_unknown_references_are_404(self):
        self.assertEqual(self.client.get(detail_url(uuid.uuid4())).status_code, 404)
        self.assertEqual(self.client.get("/api/reservations/not-a-uuid").status_code, 404)


class LabelTests(MyOrdersApiTestCase):
    def test_each_status_shows_its_label_and_who_cancelled(self):
        cases = [
            (make_order(), "Reserved", None),
            (make_order(status=Status.COLLECTED, actor=Actor.SHOP), "Paid", None),
            (make_order(status=Status.CANCELLED, actor=Actor.CUSTOMER), "Cancelled", "Customer"),
            (make_order(status=Status.CANCELLED, actor=Actor.SHOP, reason="Ran out"), "Cancelled", "Restaurant"),
            (make_order(status=Status.CANCELLED, actor=Actor.ADMIN, reason="Closed"), "Cancelled", "Admin"),
            (make_order(status=Status.EXPIRED, actor=Actor.SYSTEM), "Expired", None),
        ]
        for order, label, cancelled_by in cases:
            body = self.client.get(detail_url(order)).json()
            self.assertEqual((body["status_label"], body["cancelled_by"]), (label, cancelled_by))
        paid = self.client.get(detail_url(cases[1][0])).json()
        self.assertIsNotNone(paid["collected_at"])


class WhoCanReadTests(MyOrdersApiTestCase):
    def test_signed_out_and_bad_tokens_are_refused(self):
        order = make_order()
        for credentials in ({}, {"HTTP_AUTHORIZATION": "Bearer not-a-token"}):
            client = APIClient()
            client.credentials(**credentials)
            self.assertEqual(client.get(LIST_URL).status_code, 401)
            self.assertEqual(client.get(detail_url(order)).status_code, 401)

    def test_shop_owners_and_admins_are_refused(self):
        order = make_order()
        for role in ("SHOP_OPERATOR", "ADMIN"):
            client = self.as_user(user_id=CUSTOMER_ID, role=role)
            self.assertEqual(client.get(LIST_URL).status_code, 403)
            self.assertEqual(client.get(detail_url(order)).status_code, 403)

    def test_reading_is_read_only(self):
        order = make_order()
        self.assertEqual(self.client.delete(detail_url(order)).status_code, 405)
        self.assertEqual(self.client.post(detail_url(order), {}, format="json").status_code, 405)
        self.assertTrue(Order.objects.filter(pk=order.pk).exists())


class SavedPlaceTests(CheckoutTestCase):
    """The pickup address and phone are saved with the order at checkout."""

    def setUp(self):
        super().setUp()
        offer = self.market.offers[41]
        offer["shop"] = {"id": 3, "name": "Cedar Bakery", "public_contact": "+961 1 234 567", "branch_count": 2}
        offer["branch"] = {"id": 7, "name": "Hamra", "address": "12 Bliss Street, Beirut"}
        offer["pickup_address"] = "12 Bliss Street, Beirut"

    def test_checkout_saves_shop_branch_address_and_phone(self):
        ref = self.reserve((41, 1)).json()["reservation_ref"]
        order = Order.objects.get(reservation_ref=ref)
        self.assertEqual((order.shop_name, order.branch_name, order.pickup_address, order.shop_phone),
                         ("Cedar Bakery", "Hamra", "12 Bliss Street, Beirut", "+961 1 234 567"))
        body = self.client.get(detail_url(ref)).json()
        self.assertEqual((body["shop_name"], body["branch_name"], body["pickup_address"], body["shop_phone"]),
                         ("Cedar Bakery", "Hamra", "12 Bliss Street, Beirut", "+961 1 234 567"))

    def test_later_changes_in_marketplace_never_rewrite_it(self):
        ref = self.reserve((41, 1)).json()["reservation_ref"]
        self.market.offers[41]["branch"]["address"] = "Moved to Verdun"
        self.market.offers[41]["shop"]["public_contact"] = "+961 3 000 000"
        body = self.client.get(detail_url(ref)).json()
        self.assertEqual((body["pickup_address"], body["shop_phone"]), ("12 Bliss Street, Beirut", "+961 1 234 567"))

    def test_one_branch_restaurants_are_not_named_twice(self):
        self.market.offers[41]["shop"]["branch_count"] = 1
        ref = self.reserve((41, 1)).json()["reservation_ref"]
        self.assertEqual(Order.objects.get(reservation_ref=ref).branch_name, "")

    def test_the_offer_address_is_used_when_there_is_no_branch(self):
        self.market.offers[41]["branch"] = None
        ref = self.reserve((41, 1)).json()["reservation_ref"]
        self.assertEqual(Order.objects.get(reservation_ref=ref).pickup_address, "12 Bliss Street, Beirut")

    def test_orders_from_before_lp78_have_empty_fields(self):
        order = make_order()
        body = self.client.get(detail_url(order)).json()
        self.assertEqual((body["shop_name"], body["pickup_address"], body["shop_phone"]), ("", "", ""))
