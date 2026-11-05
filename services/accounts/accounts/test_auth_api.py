import base64
import json
from unittest import mock

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import IntegrityError
from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework.throttling import SimpleRateThrottle
from rest_framework_simplejwt.tokens import AccessToken

User = get_user_model()

PASSWORD = "Local-test-only!582Orbit"


def registration(**overrides):
    data = {"display_name": "Cedar Crumb Bakery", "email": "owner@example.test", "password": PASSWORD}
    data.update(overrides)
    return data


class AuthAPITestCase(APITestCase):
    def setUp(self):
        # Throttle counters live in the cache; start every test from zero.
        cache.clear()

    def sign_in(self, email="owner@example.test", password=PASSWORD):
        return self.client.post(reverse("auth-token"), {"email": email, "password": password}, format="json")


class RegistrationTests(AuthAPITestCase):
    url = reverse("auth-register")

    def test_registers_shop_operator_with_hashed_password_and_tokens(self):
        response = self.client.post(self.url, registration(email="  Owner@Example.TEST "), format="json")

        self.assertEqual(response.status_code, 201)
        user = User.objects.get()
        self.assertEqual(user.email, "owner@example.test")
        self.assertEqual(user.role, User.Role.SHOP_OPERATOR)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertNotEqual(user.password, PASSWORD)
        self.assertTrue(user.check_password(PASSWORD))
        self.assertEqual(
            response.data["user"],
            {"id": user.id, "email": "owner@example.test", "display_name": "Cedar Crumb Bakery", "role": "SHOP_OPERATOR", "is_staff": False},
        )
        self.assertEqual(AccessToken(response.data["access"])["user_id"], str(user.id))
        self.assertNotIn(PASSWORD, response.content.decode())

    def test_rejects_supplied_role_and_privilege_fields(self):
        for field, value in (
            ("role", "SHOP_OPERATOR"),
            ("role", "CUSTOMER"),
            ("role", "ADMIN"),
            ("is_staff", True),
            ("is_superuser", True),
            ("is_active", False),
            ("approval_status", "APPROVED"),
            ("groups", [1]),
        ):
            with self.subTest(field=field, value=value):
                response = self.client.post(self.url, registration(**{field: value}), format="json")
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.data[field], ["This field is not allowed."])
        self.assertFalse(User.objects.exists())

    def test_rejects_case_variant_duplicate_email(self):
        User.objects.create_user(email="owner@example.test", password=PASSWORD, display_name="First")

        response = self.client.post(self.url, registration(email="OWNER@example.test"), format="json")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["email"], ["An account with this email already exists."])
        self.assertEqual(User.objects.count(), 1)

    def test_concurrent_duplicate_returns_field_error_not_server_error(self):
        with mock.patch.object(User.objects, "create_user", side_effect=IntegrityError):
            response = self.client.post(self.url, registration(), format="json")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["email"], ["An account with this email already exists."])

    def test_enforces_password_validators_without_echoing_password(self):
        for password in ("Sh0rt!", "password123", "48207719365", "owner@example.test"):
            with self.subTest(password=password):
                response = self.client.post(self.url, registration(password=password), format="json")
                self.assertEqual(response.status_code, 400)
                self.assertIn("password", response.data)
                self.assertNotIn(password, response.content.decode())
        self.assertFalse(User.objects.exists())

    def test_requires_display_name_email_and_valid_email(self):
        for overrides in ({"display_name": "   "}, {"email": "not-an-email"}, {"email": ""}):
            with self.subTest(overrides=overrides):
                response = self.client.post(self.url, registration(**overrides), format="json")
                self.assertEqual(response.status_code, 400)
                self.assertIn(next(iter(overrides)), response.data)
        self.assertFalse(User.objects.exists())

    def test_ambiguous_retry_is_resolved_by_signing_in_not_by_duplicate_error(self):
        # First attempt succeeded but its response was lost (timeout).
        self.client.post(self.url, registration(), format="json")

        retry = self.client.post(self.url, registration(), format="json")
        self.assertEqual(retry.status_code, 400)  # "email exists" alone proves nothing
        self.assertNotIn("access", retry.data)

        recovered = self.sign_in()  # the caller proves ownership with the password
        self.assertEqual(recovered.status_code, 200)
        self.assertEqual(self.sign_in(password="Someone-elses-guess-77").status_code, 401)
        self.assertEqual(User.objects.count(), 1)


class SignInTests(AuthAPITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email="owner@example.test", password=PASSWORD, display_name="Owner", role=User.Role.SHOP_OPERATOR
        )

    def test_case_insensitive_email_sign_in_returns_rs256_tokens_with_claims(self):
        response = self.sign_in(email="OWNER@Example.test")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(jwt.get_unverified_header(response.data["access"])["alg"], "RS256")
        access = AccessToken(response.data["access"])
        self.assertEqual(access["user_id"], str(self.user.id))
        self.assertEqual(access["role"], "SHOP_OPERATOR")
        self.assertEqual(access["display_name"], "Owner")
        self.assertIs(access["is_staff"], False)
        self.assertIn("refresh", response.data)

    def test_credential_failures_share_one_generic_response(self):
        User.objects.create_user(email="inactive@example.test", password=PASSWORD, display_name="Off", is_active=False)
        attempts = {
            "wrong password": ("owner@example.test", "Wrong-password-91"),
            "unknown email": ("nobody@example.test", PASSWORD),
            "inactive account": ("inactive@example.test", PASSWORD),
        }
        bodies = set()
        for label, (email, password) in attempts.items():
            with self.subTest(label):
                response = self.sign_in(email, password)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.data["detail"], "Email or password is incorrect.")
                bodies.add(response.content)
        self.assertEqual(len(bodies), 1)

    def test_me_requires_a_valid_access_token(self):
        me = reverse("users-me")
        self.assertEqual(self.client.get(me).status_code, 401)
        self.client.credentials(HTTP_AUTHORIZATION="Bearer not-a-token")
        self.assertEqual(self.client.get(me).status_code, 401)

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.sign_in().data['access']}")
        response = self.client.get(me)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["email"], "owner@example.test")

    def test_rejects_tokens_not_signed_by_the_accounts_private_key(self):
        claims = jwt.decode(self.sign_in().data["access"], options={"verify_signature": False})
        other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
        segment = lambda part: base64.urlsafe_b64encode(json.dumps(part).encode()).rstrip(b"=").decode()
        forged = {
            "other RSA key": jwt.encode(claims, other_key, algorithm="RS256"),
            "unsigned (alg none)": f"{segment({'alg': 'none', 'typ': 'JWT'})}.{segment(claims)}.",
        }
        for label, token in forged.items():
            with self.subTest(label):
                self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
                self.assertEqual(self.client.get(reverse("users-me")).status_code, 401)


class SignOutTests(AuthAPITestCase):
    def setUp(self):
        super().setUp()
        User.objects.create_user(email="owner@example.test", password=PASSWORD, display_name="Owner")
        tokens = self.sign_in().data
        self.access, self.refresh = tokens["access"], tokens["refresh"]

    def post(self, name, token):
        return self.client.post(reverse(name), {"refresh": token}, format="json")

    def test_refresh_works_until_sign_out_then_is_rejected(self):
        refreshed = self.post("auth-token-refresh", self.refresh)
        self.assertEqual(refreshed.status_code, 200)
        self.assertIn("access", refreshed.data)

        self.assertEqual(self.post("auth-logout", self.refresh).status_code, 200)

        self.assertEqual(self.post("auth-token-refresh", self.refresh).status_code, 401)
        self.assertEqual(self.post("auth-logout", self.refresh).status_code, 401)

    def test_issued_access_token_stays_valid_until_expiry_after_sign_out(self):
        # Documents a known limitation: blacklisting the refresh token stops new
        # access tokens, but an access token already issued keeps working until
        # it expires (15 minutes). The web frontend must discard its copy.
        self.assertEqual(self.post("auth-logout", self.refresh).status_code, 200)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.access}")
        self.assertEqual(self.client.get(reverse("users-me")).status_code, 200)


@mock.patch.object(SimpleRateThrottle, "timer", mock.Mock(return_value=1_000_000.0))
class ThrottleTests(AuthAPITestCase):
    def rates(self, **rates):
        return mock.patch.dict(SimpleRateThrottle.THROTTLE_RATES, rates)

    def test_registration_is_limited_per_client(self):
        with self.rates(auth_register="2/hour"):
            for i in range(2):
                self.assertEqual(self.client.post(reverse("auth-register"), registration(email=f"o{i}@example.test"), format="json").status_code, 201)
            blocked = self.client.post(reverse("auth-register"), registration(email="o3@example.test"), format="json")
        self.assertEqual(blocked.status_code, 429)
        self.assertIn("Retry-After", blocked)
        self.assertFalse(User.objects.filter(email="o3@example.test").exists())

    def test_sign_in_is_limited_per_email_even_with_the_right_password(self):
        User.objects.create_user(email="owner@example.test", password=PASSWORD, display_name="Owner")
        with self.rates(auth_sign_in="100/minute", auth_sign_in_email="2/minute"):
            for _ in range(2):
                self.assertEqual(self.sign_in(password="Wrong-password-91").status_code, 401)
            self.assertEqual(self.sign_in(email="OWNER@example.test").status_code, 429)
            self.assertEqual(self.sign_in(email="other@example.test").status_code, 401)  # other accounts unaffected
            with mock.patch.object(SimpleRateThrottle, "timer", mock.Mock(return_value=1_000_061.0)):
                self.assertEqual(self.sign_in().status_code, 200)  # window has passed

    def test_sign_in_is_limited_per_client(self):
        with self.rates(auth_sign_in="2/minute", auth_sign_in_email="100/minute"):
            self.assertEqual(self.sign_in(email="a@example.test").status_code, 401)
            self.assertEqual(self.sign_in(email="b@example.test").status_code, 401)
            self.assertEqual(self.sign_in(email="c@example.test").status_code, 429)
