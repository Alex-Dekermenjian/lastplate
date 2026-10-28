from django.contrib.auth import SESSION_KEY, get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse


User = get_user_model()


class UserAccountTests(TestCase):
    def test_email_is_normalized_and_password_is_hashed(self):
        user = User.objects.create_user(
            email="  Alex@Example.TEST  ",
            password="Local-test-only!582Orbit",
            display_name="Alex",
        )
        user.refresh_from_db()
        self.assertEqual(user.email, "alex@example.test")
        self.assertNotEqual(user.password, "Local-test-only!582Orbit")
        self.assertTrue(user.check_password("Local-test-only!582Orbit"))
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

    def test_email_is_required(self):
        with self.assertRaises(ValueError):
            User.objects.create_user(email="", display_name="Alex")

    def test_database_rejects_case_variant_duplicate_email(self):
        User.objects.create_user(email="alex@example.test", display_name="Alex")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                # Bypass the manager's normalization to exercise the DB constraint.
                User.objects.create(email="ALEX@example.test", display_name="Other")

    def test_create_superuser_grants_admin_flags(self):
        user = User.objects.create_superuser(
            email="admin@example.test",
            password="Local-test-only!582Orbit",
            display_name="Admin",
        )
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.is_active)

    def test_create_superuser_rejects_missing_admin_flags(self):
        for flags in ({"is_staff": False}, {"is_superuser": False}):
            with self.subTest(flags=flags):
                with self.assertRaises(ValueError):
                    User.objects.create_superuser(
                        email="admin@example.test", display_name="Admin", **flags
                    )


class AdminAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(
            email="admin@example.test",
            password="Local-test-only!582Orbit",
            display_name="Admin",
        )
        cls.customer = User.objects.create_user(
            email="customer@example.test",
            password="Local-test-only!582Orbit",
            display_name="Customer",
        )

    def test_anonymous_visitor_is_redirected_to_login(self):
        response = self.client.get(reverse("admin:index"))
        self.assertRedirects(
            response, reverse("admin:login") + "?next=" + reverse("admin:index")
        )

    def test_admin_can_login_with_case_variant_email_and_logout(self):
        response = self.client.post(
            reverse("admin:login"),
            {"username": "ADMIN@EXAMPLE.TEST", "password": "Local-test-only!582Orbit"},
        )
        self.assertRedirects(response, reverse("admin:index"))
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 200)
        self.assertEqual(self.client.session[SESSION_KEY], str(self.admin.pk))
        self.client.post(reverse("admin:logout"))
        self.assertNotIn(SESSION_KEY, self.client.session)
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 302)

    def test_wrong_password_does_not_create_authenticated_session(self):
        response = self.client.post(
            reverse("admin:login"),
            {"username": "admin@example.test", "password": "Incorrect-test-password"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(SESSION_KEY, self.client.session)

    def test_nonstaff_account_cannot_login_to_admin(self):
        response = self.client.post(
            reverse("admin:login"),
            {"username": "customer@example.test", "password": "Local-test-only!582Orbit"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(SESSION_KEY, self.client.session)
