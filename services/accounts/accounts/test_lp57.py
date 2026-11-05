"""LP-57: the Admin role. Admins are created only with the create_admin
management command, and their access token says role ADMIN."""

from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.urls import reverse
from rest_framework_simplejwt.tokens import AccessToken

from .management.commands.create_admin import PASSWORD_ENV
from .test_auth_api import PASSWORD, AuthAPITestCase, registration

User = get_user_model()


def create_admin(email="Admin@Example.test", name="LastPlate Admin", password=PASSWORD):
    with mock.patch.dict("os.environ", {PASSWORD_ENV: password}):
        call_command("create_admin", email=email, name=name, stdout=StringIO())


class CreateAdminCommandTests(AuthAPITestCase):
    def test_creates_an_admin_without_django_admin_rights(self):
        create_admin()

        user = User.objects.get()
        self.assertEqual(user.email, "admin@example.test")
        self.assertEqual(user.display_name, "LastPlate Admin")
        self.assertEqual(user.role, User.Role.ADMIN)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertTrue(user.check_password(PASSWORD))

    def test_refuses_an_email_that_already_has_an_account(self):
        User.objects.create_user("admin@example.test", PASSWORD, display_name="Rami")

        with self.assertRaisesMessage(CommandError, "already exists"):
            create_admin(email="ADMIN@example.test")
        self.assertEqual(User.objects.get().role, User.Role.CUSTOMER)

    def test_refuses_a_weak_password_or_bad_input(self):
        for kwargs in ({"password": "12345678"}, {"email": "not-an-email"}, {"name": "   "}):
            with self.subTest(**kwargs), self.assertRaises(CommandError):
                create_admin(**kwargs)
        self.assertFalse(User.objects.exists())

    def test_interactive_passwords_must_match(self):
        with mock.patch.dict("os.environ", {PASSWORD_ENV: ""}), \
                mock.patch("getpass.getpass", side_effect=[PASSWORD, PASSWORD + "x"]), \
                self.assertRaisesMessage(CommandError, "didn't match"):
            call_command("create_admin", email="admin@example.test", name="Admin", stdout=StringIO())
        self.assertFalse(User.objects.exists())

    def test_admin_signs_in_with_an_admin_token(self):
        create_admin()

        response = self.client.post(
            reverse("auth-token"), {"email": "admin@example.test", "password": PASSWORD}, format="json"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(AccessToken(response.data["access"])["role"], "ADMIN")
        me = self.client.get(reverse("users-me"), HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")
        self.assertEqual(me.data["role"], "ADMIN")


class PublicSignUpCannotCreateAdminsTests(AuthAPITestCase):
    def test_both_sign_up_endpoints_reject_an_admin_role(self):
        for url in (reverse("auth-register"), reverse("auth-register-customer")):
            with self.subTest(url=url):
                response = self.client.post(url, registration(role="ADMIN"), format="json")
                self.assertEqual(response.status_code, 400)
                self.assertIn("role", response.data)
        self.assertFalse(User.objects.filter(role=User.Role.ADMIN).exists())
