"""LP-18: a signed-in user may change their own display name, and only that."""

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import AccessToken

User = get_user_model()
PASSWORD = "Local-test-only!582Orbit"


class ChangeDisplayNameTests(APITestCase):
    url = reverse("users-me")

    def setUp(self):
        self.user = User.objects.create_user(
            email="rami@example.test", password=PASSWORD, display_name="Rami", role=User.Role.CUSTOMER
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(self.user)}")

    def test_changes_the_display_name(self):
        response = self.client.patch(self.url, {"display_name": "Rami K."}, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["display_name"], "Rami K.")
        self.user.refresh_from_db()
        self.assertEqual(self.user.display_name, "Rami K.")

    def test_blank_and_too_long_names_are_refused(self):
        for name in ("", "   ", "x" * 151):
            with self.subTest(name=name[:5]):
                response = self.client.patch(self.url, {"display_name": name}, format="json")
                self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertEqual(self.user.display_name, "Rami")

    def test_email_role_and_staff_flags_cannot_be_changed(self):
        for field, value in (("email", "new@example.test"), ("role", "ADMIN"), ("is_staff", True)):
            with self.subTest(field=field):
                response = self.client.patch(self.url, {"display_name": "Rami", field: value}, format="json")
                self.assertEqual(response.status_code, 400)
                self.assertIn(field, response.data)
        self.user.refresh_from_db()
        self.assertEqual((self.user.email, self.user.role, self.user.is_staff), ("rami@example.test", "CUSTOMER", False))

    def test_needs_a_signed_in_user(self):
        self.client.credentials()
        self.assertEqual(self.client.patch(self.url, {"display_name": "X"}, format="json").status_code, 401)

    def test_the_name_is_trimmed_and_only_the_signed_in_user_changes(self):
        other = User.objects.create_user(
            email="other@example.test", password=PASSWORD, display_name="Other", role=User.Role.CUSTOMER
        )

        response = self.client.patch(self.url, {"display_name": "  Rami K.  "}, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["display_name"], "Rami K.")
        other.refresh_from_db()
        self.assertEqual(other.display_name, "Other")


class OneEmailOneAccountTests(APITestCase):
    """LP-18: an email belongs to one account, whatever its capital letters."""

    def setUp(self):
        self.user = User.objects.create_user(
            email="rami@example.test", password=PASSWORD, display_name="Rami", role=User.Role.CUSTOMER
        )
        self.taken = User.objects.create_user(
            email="taken@example.test", password=PASSWORD, display_name="Taken", role=User.Role.CUSTOMER
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(self.user)}")

    def test_profile_cannot_take_another_accounts_email_in_any_case(self):
        for email in ("taken@example.test", "TAKEN@Example.TEST"):
            with self.subTest(email=email):
                response = self.client.patch(
                    reverse("users-me"), {"display_name": "Rami", "email": email}, format="json"
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn("email", response.data)
        self.user.refresh_from_db()
        self.taken.refresh_from_db()
        self.assertEqual((self.user.email, self.taken.email), ("rami@example.test", "taken@example.test"))

    def test_the_database_refuses_a_changed_email_that_clashes_in_any_case(self):
        self.user.email = "TAKEN@example.test"

        with self.assertRaises(IntegrityError), transaction.atomic():
            self.user.save()

        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "rami@example.test")

    def test_customer_sign_up_refuses_an_email_already_used_in_any_case(self):
        before = User.objects.count()
        for email in ("rami@example.test", "RAMI@EXAMPLE.TEST", "  Rami@example.test "):
            with self.subTest(email=email):
                self.client.credentials()
                response = self.client.post(
                    reverse("auth-register-customer"),
                    {"display_name": "Second Rami", "email": email, "password": PASSWORD},
                    format="json",
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn("email", response.data)
        self.assertEqual(User.objects.count(), before)