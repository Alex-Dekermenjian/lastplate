import base64
import hashlib
import hmac
import json
import threading
import time

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.db import IntegrityError, connection, transaction
from django.test import TransactionTestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient, APITestCase

from .models import Shop


def make_key_pair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    public = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    return private, public.decode()


# The tests play the role of Accounts with their own key pair; Marketplace
# only ever sees the public half through settings.JWT_PUBLIC_KEY.
TEST_PRIVATE_KEY, TEST_PUBLIC_KEY = make_key_pair()
OTHER_PRIVATE_KEY, _ = make_key_pair()


def make_token(user_id=7, role="SHOP_OPERATOR", token_type="access", expires_in=900, key=TEST_PRIVATE_KEY, **claims):
    now = int(time.time())
    payload = {"token_type": token_type, "exp": now + expires_in, "iat": now, "jti": "test-jti",
               "user_id": str(user_id), "role": role, "display_name": "Owner", "is_staff": False}
    payload.update(claims)
    return jwt.encode(payload, key, algorithm="RS256")


def b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


@override_settings(JWT_PUBLIC_KEY=TEST_PUBLIC_KEY)
class ShopAPITestCase(APITestCase):
    create_url = reverse("shop-create")
    me_url = reverse("shop-me")

    def as_user(self, token):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    def create(self, data=None, **token_args):
        self.as_user(make_token(**token_args))
        return self.client.post(self.create_url, data if data is not None else {"name": "Cedar Crumb Bakery"}, format="json")


class TokenVerificationTests(ShopAPITestCase):
    def assert_rejected(self, token):
        self.as_user(token)
        for method, url in (("post", self.create_url), ("get", self.me_url)):
            response = getattr(self.client, method)(url, {"name": "X"} if method == "post" else None, format="json")
            self.assertEqual(response.status_code, 401, f"{method} {url}")
        self.assertFalse(Shop.objects.exists())

    def test_requires_a_bearer_token(self):
        self.assertEqual(self.client.post(self.create_url, {"name": "X"}, format="json").status_code, 401)
        self.assertEqual(self.client.get(self.me_url).status_code, 401)

    def test_rejects_invalid_expired_and_wrong_type_tokens(self):
        claims = {"token_type": "access", "exp": int(time.time()) + 900, "user_id": "7", "role": "SHOP_OPERATOR"}
        header_none = b64(json.dumps({"alg": "none", "typ": "JWT"}).encode())
        header_hs256 = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
        body = b64(json.dumps(claims).encode())
        # HS256 "signed" with the public key as the HMAC secret (algorithm confusion).
        confused_sig = b64(hmac.new(TEST_PUBLIC_KEY.encode(), f"{header_hs256}.{body}".encode(), hashlib.sha256).digest())
        cases = {
            "malformed": "not-a-token",
            "signed by another key": make_token(key=OTHER_PRIVATE_KEY),
            "expired": make_token(expires_in=-10),
            "refresh token": make_token(token_type="refresh"),
            "unsigned alg none": f"{header_none}.{body}.",
            "HS256 with public key": f"{header_hs256}.{body}.{confused_sig}",
            "non-numeric user_id": make_token(user_id="abc"),
            "missing user_id": jwt.encode({k: v for k, v in claims.items() if k != "user_id"}, TEST_PRIVATE_KEY, algorithm="RS256"),
        }
        for label, token in cases.items():
            with self.subTest(label):
                self.assert_rejected(token)

    def test_rejects_extra_parts_in_authorization_header(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {make_token()} extra")
        self.assertEqual(self.client.get(self.me_url).status_code, 401)


class RoleTests(ShopAPITestCase):
    def test_only_shop_operators_can_create_or_read_their_shop(self):
        for role in ("CUSTOMER", "", "ADMIN"):
            with self.subTest(role=role):
                self.assertEqual(self.create(role=role).status_code, 403)
                self.assertEqual(self.client.get(self.me_url).status_code, 403)
        self.assertFalse(Shop.objects.exists())


class CreateShopTests(ShopAPITestCase):
    def test_creates_pending_shop_owned_by_the_token_user(self):
        response = self.create(user_id=7)

        self.assertEqual(response.status_code, 201)
        shop = Shop.objects.get()
        self.assertEqual(shop.operator_user_id, 7)
        self.assertEqual(shop.name, "Cedar Crumb Bakery")
        self.assertEqual(shop.approval_status, Shop.ApprovalStatus.PENDING)
        self.assertEqual(response.data["id"], shop.id)
        self.assertEqual(response.data["approval_status"], "PENDING")

    def test_rejects_submitted_ownership_status_and_other_fields(self):
        for field, value in (
            ("operator_user_id", 99),
            ("approval_status", "APPROVED"),
            ("id", 1),
            ("created_at", "2026-01-01T00:00:00Z"),
        ):
            with self.subTest(field=field):
                response = self.create({"name": "Cedar Crumb Bakery", field: value})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.data[field], ["This field is not allowed."])
        self.assertFalse(Shop.objects.exists())

    def test_validates_shop_name(self):
        for data in ({}, {"name": ""}, {"name": "   "}, {"name": "x" * 151}):
            with self.subTest(data=data):
                response = self.create(data)
                self.assertEqual(response.status_code, 400)
                self.assertIn("name", response.data)
        self.assertFalse(Shop.objects.exists())

    def test_retry_returns_the_existing_shop_without_overwriting_it(self):
        first = self.create(user_id=7)
        shop = Shop.objects.get()
        shop.approval_status = Shop.ApprovalStatus.APPROVED  # e.g. approved meanwhile
        shop.save()

        retry = self.create({"name": "A different name"}, user_id=7)

        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.data["id"], first.data["id"])
        shop.refresh_from_db()
        self.assertEqual(shop.name, "Cedar Crumb Bakery")
        self.assertEqual(shop.approval_status, Shop.ApprovalStatus.APPROVED)
        self.assertEqual(Shop.objects.count(), 1)

    def test_each_operator_gets_their_own_shop(self):
        self.assertEqual(self.create({"name": "Shop A"}, user_id=7).status_code, 201)
        self.assertEqual(self.create({"name": "Shop B"}, user_id=8).status_code, 201)
        self.assertEqual(
            sorted(Shop.objects.values_list("operator_user_id", "name")), [(7, "Shop A"), (8, "Shop B")]
        )


class MyShopTests(ShopAPITestCase):
    def test_returns_404_until_the_operator_has_a_shop(self):
        self.as_user(make_token(user_id=7))
        self.assertEqual(self.client.get(self.me_url).status_code, 404)

    def test_returns_only_the_callers_own_shop(self):
        Shop.objects.create(operator_user_id=7, name="Shop A")
        Shop.objects.create(operator_user_id=8, name="Shop B")

        self.as_user(make_token(user_id=8))
        response = self.client.get(self.me_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["name"], "Shop B")
        self.assertEqual(response.data["operator_user_id"], 8)
        self.as_user(make_token(user_id=9))
        self.assertEqual(self.client.get(self.me_url).status_code, 404)


class ShopModelTests(APITestCase):
    def test_database_enforces_one_shop_per_operator_and_valid_status(self):
        Shop.objects.create(operator_user_id=7, name="Shop A")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Shop.objects.create(operator_user_id=7, name="Shop B")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Shop.objects.create(operator_user_id=8, name="Shop C", approval_status="SUSPENDED")


@override_settings(JWT_PUBLIC_KEY=TEST_PUBLIC_KEY)
class ConcurrentCreationTests(TransactionTestCase):
    def test_parallel_requests_from_one_operator_create_one_shop(self):
        token = make_token(user_id=7)
        barrier = threading.Barrier(4)
        statuses = []

        def create():
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
            try:
                barrier.wait()
                statuses.append(client.post(reverse("shop-create"), {"name": "Cedar Crumb Bakery"}, format="json").status_code)
            finally:
                connection.close()

        threads = [threading.Thread(target=create) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(sorted(statuses), [200, 200, 200, 201])
        self.assertEqual(Shop.objects.filter(operator_user_id=7).count(), 1)
