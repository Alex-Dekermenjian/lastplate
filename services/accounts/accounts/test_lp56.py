"""LP-56: customer sign-up next to shop-operator sign-up, and the staff flag
the web uses to choose where each account lands after login."""

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework_simplejwt.tokens import AccessToken

from .test_auth_api import PASSWORD, AuthAPITestCase, registration

User = get_user_model()


class CustomerRegistrationTests(AuthAPITestCase):
    url = reverse("auth-register-customer")

    def test_creates_a_customer_with_tokens_and_no_privileges(self):
        response = self.client.post(self.url, registration(display_name="Rami"), format="json")

        self.assertEqual(response.status_code, 201)
        user = User.objects.get()
        self.assertEqual(user.role, User.Role.CUSTOMER)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertTrue(user.check_password(PASSWORD))
        self.assertEqual(response.data["user"]["role"], "CUSTOMER")
        self.assertIs(response.data["user"]["is_staff"], False)
        self.assertEqual(AccessToken(response.data["access"])["role"], "CUSTOMER")

    def test_shop_operator_sign_up_still_creates_a_shop_operator(self):
        response = self.client.post(reverse("auth-register"), registration(), format="json")

        self.assertEqual(response.status_code, 201)
        self.assertEqual(User.objects.get().role, User.Role.SHOP_OPERATOR)

    def test_forged_role_or_privilege_fields_are_rejected(self):
        for field, value in (
            ("role", "ADMIN"),
            ("role", "STAFF"),
            ("role", "SHOP_OPERATOR"),
            ("is_staff", True),
            ("is_superuser", True),
        ):
            with self.subTest(field=field, value=value):
                response = self.client.post(self.url, registration(**{field: value}), format="json")
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.data[field], ["This field is not allowed."])
        self.assertFalse(User.objects.exists())

    def test_password_spaces_are_kept(self):
        spaced = f"  {PASSWORD}  "
        response = self.client.post(self.url, registration(password=spaced), format="json")

        self.assertEqual(response.status_code, 201)
        user = User.objects.get()
        self.assertTrue(user.check_password(spaced))
        self.assertFalse(user.check_password(PASSWORD))

    def test_duplicate_email_across_roles_is_rejected(self):
        self.client.post(reverse("auth-register"), registration(), format="json")

        response = self.client.post(self.url, registration(email="OWNER@example.test"), format="json")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(User.objects.count(), 1)


class MeStaffFlagTests(AuthAPITestCase):

    def test_me_reports_whether_the_account_is_staff(self):
        for is_staff in (False, True):
            with self.subTest(is_staff=is_staff):
                email = f"user-{is_staff}@example.test"
                User.objects.create_user(email=email, password=PASSWORD, display_name="U", is_staff=is_staff)
                access = self.sign_in(email=email).data["access"]

                response = self.client.get(reverse("users-me"), HTTP_AUTHORIZATION=f"Bearer {access}")

                self.assertEqual(response.status_code, 200)
                self.assertIs(response.data["is_staff"], is_staff)
