"""LP-14: Permission and validation audit tests for the Accounts service."""

import base64
import hashlib
import hmac
import json
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.urls import reverse
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from .test_auth_api import PASSWORD, AuthAPITestCase, User, registration


def segment(part):
    return base64.urlsafe_b64encode(json.dumps(part).encode()).rstrip(b"=").decode()


def unsegment(text):
    return json.loads(base64.urlsafe_b64decode(text + "=" * (-len(text) % 4)))


class AccessTokenRejectionTests(AuthAPITestCase):
    """/api/users/me/ is the only token-protected Accounts endpoint."""

    def setUp(self):
        super().setUp()
        self.owner = User.objects.create_user(
            email="owner@example.test", password=PASSWORD, display_name="Owner", role=User.Role.SHOP_OPERATOR
        )
        self.other = User.objects.create_user(
            email="other@example.test", password=PASSWORD, display_name="Other"
        )
        self.me = reverse("users-me")

    def get_me(self, token):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        return self.client.get(self.me)

    def test_valid_access_token_is_accepted(self):
        response = self.get_me(str(AccessToken.for_user(self.owner)))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["email"], "owner@example.test")

    def test_expired_tampered_and_wrong_type_tokens_are_rejected(self):
        expired = AccessToken.for_user(self.owner)
        expired.set_exp(lifetime=-timedelta(seconds=1))

        header, payload, signature = str(AccessToken.for_user(self.owner)).split(".")
        claims = unsegment(payload)
        claims["user_id"] = str(self.other.pk)
        tampered = f"{header}.{segment(claims)}.{signature}"

        hs_header = segment({"alg": "HS256", "typ": "JWT"})
        hs_body = segment(claims)
        confused = hmac.new(
            settings.SIMPLE_JWT["VERIFYING_KEY"].encode(), f"{hs_header}.{hs_body}".encode(), hashlib.sha256
        ).digest()

        cases = {
            "no token": None,
            "garbage": "not-a-token",
            "expired": str(expired),
            "tampered payload": tampered,
            "refresh token used as access": str(RefreshToken.for_user(self.owner)),
            "HS256 signed with the public key":
                f"{hs_header}.{hs_body}.{base64.urlsafe_b64encode(confused).rstrip(b'=').decode()}",
        }
        for label, token in cases.items():
            with self.subTest(label):
                if token is None:
                    self.client.credentials()
                    response = self.client.get(self.me)
                else:
                    response = self.get_me(token)
                self.assertEqual(response.status_code, 401)

    def test_token_of_a_deactivated_user_is_rejected(self):
        token = str(AccessToken.for_user(self.owner))
        User.objects.filter(pk=self.owner.pk).update(is_active=False)

        self.assertEqual(self.get_me(token).status_code, 401)

    def test_me_only_allows_get_and_a_display_name_patch(self):
        """LP-18: PATCH changes the display name only; a role change is refused."""
        token = str(AccessToken.for_user(self.owner))
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

        for method in ("post", "put", "delete"):
            with self.subTest(method):
                response = getattr(self.client, method)(self.me, {"role": "CUSTOMER"}, format="json")
                self.assertEqual(response.status_code, 405)
        with self.subTest("patch"):
            response = self.client.patch(self.me, {"role": "CUSTOMER"}, format="json")
            self.assertEqual(response.status_code, 400)
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.role, User.Role.SHOP_OPERATOR)


class AdminAccessTests(AuthAPITestCase):

    def test_bearer_token_gives_no_admin_access(self):
        user = User.objects.create_user(email="owner@example.test", password=PASSWORD, display_name="Owner")
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(user)}")

        response = self.client.get(reverse("admin:index"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("admin:login"), response["Location"])


class EmailValidationTests(AuthAPITestCase):
    url = reverse("auth-register")

    def test_malformed_emails_are_rejected(self):
        for email in ("plainaddress", "a@", "@example.test", "a b@example.test", "a@exa mple.test",
                      "a@@example.test", "owner@example..test", "x" * 250 + "@ex.test"):
            with self.subTest(email=email):
                response = self.client.post(self.url, registration(email=email), format="json")
                self.assertEqual(response.status_code, 400)
                self.assertIn("email", response.data)
        self.assertFalse(User.objects.exists())

    def test_email_is_unique_regardless_of_case_everywhere(self):
        self.assertEqual(self.client.post(self.url, registration(email="Owner@Example.test"), format="json").status_code, 201)

        for variant in ("owner@example.test", "OWNER@EXAMPLE.TEST", "  oWnEr@example.TEST "):
            with self.subTest(variant=variant):
                response = self.client.post(self.url, registration(email=variant), format="json")
                self.assertEqual(response.status_code, 400)
                self.assertIn("email", response.data)

        # The database refuses a case variant even if the API check is bypassed.
        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.bulk_create([User(email="OWNER@example.test", display_name="Dup")])

        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(self.sign_in(email="OWNER@EXAMPLE.TEST").status_code, 200)


class PasswordValidationTests(AuthAPITestCase):
    url = reverse("auth-register")

    def test_password_rules(self):
        cases = {
            "too short": "Ab1!xyz",
            "numbers only": "9472018365",
            "common": "qwertyuiop",
            "like the display name": "CedarCrumbBakery",
            "too long": "Aa1!" * 33,
        }
        for label, password in cases.items():
            with self.subTest(label):
                response = self.client.post(self.url, registration(password=password), format="json")
                self.assertEqual(response.status_code, 400)
                self.assertIn("password", response.data)
        self.assertFalse(User.objects.exists())

    def test_password_is_not_trimmed(self):
        password = f"  {PASSWORD}  "

        self.assertEqual(self.client.post(self.url, registration(password=password), format="json").status_code, 201)

        self.assertEqual(self.sign_in(password=password).status_code, 200)
        self.assertEqual(self.sign_in(password=PASSWORD).status_code, 401)