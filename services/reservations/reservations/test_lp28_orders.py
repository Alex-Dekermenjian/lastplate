"""LP-28: the restaurant accepts or rejects a new order. Rejecting releases
the held stock exactly once; only the owning restaurant may decide; a double
click or a race makes only one transition."""

import threading
import time
from decimal import Decimal
from unittest import mock

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.db import connection
from django.test import TransactionTestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient, APITestCase

from . import marketplace
from .models import Order, OrderLine
from .order_service import ALREADY, REASON_REQUIRED, retry_pending_releases

_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PRIVATE_KEY = _key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                 serialization.NoEncryption())
PUBLIC_KEY = _key.public_key().public_bytes(serialization.Encoding.PEM,
                                            serialization.PublicFormat.SubjectPublicKeyInfo).decode()
OWNER, OTHER_OWNER, CUSTOMER = 7, 8, 12


def token(user_id=OWNER, role="SHOP_OPERATOR"):
    now = int(time.time())
    return jwt.encode({"token_type": "access", "exp": now + 900, "iat": now, "jti": "t",
                       "user_id": str(user_id), "role": role}, PRIVATE_KEY, algorithm="RS256")


def make_order(**overrides):
    fields = {"customer_user_id": CUSTOMER, "customer_name": "Rami", "shop_id": 3, "shop_name": "Cedar Bakery",
              "shop_operator_user_id": OWNER, **overrides}
    order = Order.objects.create(**fields)
    OrderLine.objects.create(order=order, listing_id=42, title="Croissant Box", unit_price=Decimal("4.50"),
                             quantity=2)
    OrderLine.objects.create(order=order, listing_id=43, title="Evening LastBag", unit_price=Decimal("5.00"),
                             quantity=1)
    return order


def released(status=200):
    return mock.patch.object(marketplace, "release", return_value=(status, {"status": "RELEASED"}))


@override_settings(JWT_PUBLIC_KEY=PUBLIC_KEY)
class OrderAPITestCase(APITestCase):
    def setUp(self):
        self.order = make_order()
        self.as_user(OWNER)

    def as_user(self, user_id, role="SHOP_OPERATOR"):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token(user_id, role)}")

    def accept(self, order=None):
        return self.client.post(reverse("shop-order-accept", args=[(order or self.order).ref]))

    def reject(self, reason="Sold out early, sorry.", order=None):
        return self.client.post(reverse("shop-order-reject", args=[(order or self.order).ref]),
                                {"reason": reason}, format="json")

    def refresh(self):
        self.order.refresh_from_db()
        return self.order


class ListTests(OrderAPITestCase):
    def test_lists_only_this_restaurants_new_orders_with_lines_and_total(self):
        make_order(shop_operator_user_id=OTHER_OWNER)
        make_order(status=Order.Status.ACCEPTED)

        response = self.client.get(reverse("shop-orders"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual([o["ref"] for o in response.data], [str(self.order.ref)])
        self.assertEqual(response.data[0]["total"], "14.00")
        self.assertEqual(len(response.data[0]["lines"]), 2)
        self.assertNotIn("customer_user_id", response.data[0])


class AcceptTests(OrderAPITestCase):
    def test_owner_accepts_without_touching_stock(self):
        with released() as release:
            response = self.accept()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.refresh().status, Order.Status.ACCEPTED)
        self.assertIsNotNone(self.order.decided_at)
        release.assert_not_called()

    def test_a_double_click_gives_a_clear_message_and_one_transition(self):
        self.accept()
        response = self.accept()

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["detail"], ALREADY[Order.Status.ACCEPTED])
        with released() as release:
            self.assertEqual(self.reject().status_code, 409)
        release.assert_not_called()
        self.assertEqual(self.refresh().status, Order.Status.ACCEPTED)

    def test_an_order_the_customer_just_cancelled_cannot_be_accepted(self):
        Order.objects.filter(pk=self.order.pk).update(status=Order.Status.CANCELLED)

        response = self.accept()

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["detail"], ALREADY[Order.Status.CANCELLED])


class RejectTests(OrderAPITestCase):
    def test_reject_needs_a_reason(self):
        for reason in ("", "   "):
            response = self.reject(reason)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.data["detail"], REASON_REQUIRED)
        self.assertEqual(self.refresh().status, Order.Status.PENDING)

    def test_reject_stores_the_reason_and_releases_each_line_once(self):
        with released() as release:
            response = self.reject("  Sold out early, sorry.  ")
            self.reject()  # a double click

        self.assertEqual(response.status_code, 200)
        order = self.refresh()
        self.assertEqual(order.status, Order.Status.REJECTED)
        self.assertEqual(order.rejection_reason, "Sold out early, sorry.")
        self.assertEqual(order.sync_state, Order.SyncState.SYNCED)
        self.assertEqual(sorted(call.args[1] for call in release.call_args_list), [42, 43])

    def test_marketplace_down_keeps_the_rejection_and_retries_the_release(self):
        with mock.patch.object(marketplace, "release", side_effect=marketplace.MarketplaceUnavailable):
            self.assertEqual(self.reject().status_code, 200)
        self.assertEqual(self.refresh().status, Order.Status.REJECTED)
        self.assertEqual(self.order.sync_state, Order.SyncState.RELEASE_PENDING)

        with released() as release:
            retry_pending_releases()
            retry_pending_releases()  # nothing left to do

        self.assertEqual(release.call_count, 2)
        self.assertEqual(self.refresh().sync_state, Order.SyncState.SYNCED)

    def test_an_impossible_answer_needs_attention(self):
        with released(status=409):
            self.reject()
        self.assertEqual(self.refresh().sync_state, Order.SyncState.NEEDS_ATTENTION)


class PermissionTests(OrderAPITestCase):
    def test_another_restaurant_gets_not_found(self):
        self.as_user(OTHER_OWNER)
        with released() as release:
            self.assertEqual(self.accept().status_code, 404)
            self.assertEqual(self.reject().status_code, 404)
        release.assert_not_called()
        self.assertEqual(self.refresh().status, Order.Status.PENDING)

    def test_customers_and_admins_are_refused(self):
        for role in ("CUSTOMER", "ADMIN"):
            self.as_user(OWNER, role)
            with self.subTest(role=role):
                self.assertEqual(self.accept().status_code, 403)
                self.assertEqual(self.client.get(reverse("shop-orders")).status_code, 403)
        self.assertEqual(self.refresh().status, Order.Status.PENDING)

    def test_no_token_or_a_forged_token(self):
        forged = jwt.encode({"token_type": "access", "exp": int(time.time()) + 900, "user_id": str(OWNER),
                             "role": "SHOP_OPERATOR"}, "secret", algorithm="HS256")
        for credentials in ({}, {"HTTP_AUTHORIZATION": f"Bearer {forged}"}):
            self.client.credentials(**credentials)
            self.assertEqual(self.accept().status_code, 401)
        self.assertEqual(self.refresh().status, Order.Status.PENDING)


@override_settings(JWT_PUBLIC_KEY=PUBLIC_KEY)
class RaceTests(TransactionTestCase):
    def test_accept_and_reject_at_the_same_moment_make_one_transition(self):
        order = make_order()
        results = []

        def act(path, data):
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION=f"Bearer {token()}")
            results.append(client.post(reverse(path, args=[order.ref]), data, format="json").status_code)
            connection.close()

        with released() as release:
            threads = [threading.Thread(target=act, args=("shop-order-accept", {})) for _ in range(3)]
            threads += [threading.Thread(target=act, args=("shop-order-reject", {"reason": "Closed."}))
                        for _ in range(3)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

        self.assertEqual(sorted(results), [200, 409, 409, 409, 409, 409])
        order.refresh_from_db()
        # Stock is released only if the winner was a rejection, and then once per line.
        self.assertEqual(release.call_count, 2 if order.status == Order.Status.REJECTED else 0)
