"""LP-50: "Keep me logged in" for 30 days, and temporary passwords that an
admin sets from /console (there is no email sending)."""

from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from .models import TemporaryPasswordLog
from .test_auth_api import PASSWORD, AuthAPITestCase

User = get_user_model()
NEW_PASSWORD = "Fresh-start!731Harbour"


def lifetime(refresh_token):
    token = RefreshToken(refresh_token)
    return timedelta(seconds=token["exp"] - token["iat"])


class KeepMeLoggedInTests(AuthAPITestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user("rami@example.test", PASSWORD, display_name="Rami")

    def sign_in_with(self, **extra):
        response = self.client.post(
            reverse("auth-token"), {"email": "rami@example.test", "password": PASSWORD, **extra}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        return response.data

    def test_ticked_box_gives_a_30_day_refresh_token(self):
        tokens = self.sign_in_with(remember_me=True)
        self.assertEqual(lifetime(tokens["refresh"]), timedelta(days=30))
        # The access token is still short-lived.
        access = AccessToken(tokens["access"])
        self.assertEqual(timedelta(seconds=access["exp"] - access["iat"]), timedelta(minutes=15))

    def test_without_the_box_the_refresh_token_keeps_its_1_day_lifetime(self):
        for extra in ({}, {"remember_me": False}):
            with self.subTest(**extra):
                self.assertEqual(lifetime(self.sign_in_with(**extra)["refresh"]), timedelta(days=1))

    def test_30_day_token_still_refreshes_after_a_week_and_ends_after_30_days(self):
        refresh = self.sign_in_with(remember_me=True)["refresh"]
        later = timezone.now() + timedelta(days=7)
        with mock.patch("rest_framework_simplejwt.tokens.aware_utcnow", return_value=later):
            response = self.client.post(reverse("auth-token-refresh"), {"refresh": refresh}, format="json")
        self.assertEqual(response.status_code, 200)

        too_late = timezone.now() + timedelta(days=30, minutes=1)
        with mock.patch("rest_framework_simplejwt.tokens.aware_utcnow", return_value=too_late):
            response = self.client.post(reverse("auth-token-refresh"), {"refresh": refresh}, format="json")
        self.assertEqual(response.status_code, 401)

    def test_normal_token_ends_after_1_day(self):
        refresh = self.sign_in_with()["refresh"]
        too_late = timezone.now() + timedelta(days=1, minutes=1)
        with mock.patch("rest_framework_simplejwt.tokens.aware_utcnow", return_value=too_late):
            response = self.client.post(reverse("auth-token-refresh"), {"refresh": refresh}, format="json")
        self.assertEqual(response.status_code, 401)

    def test_signing_out_revokes_a_30_day_token(self):
        refresh = self.sign_in_with(remember_me=True)["refresh"]
        self.assertEqual(self.client.post(reverse("auth-logout"), {"refresh": refresh}, format="json").status_code, 200)
        response = self.client.post(reverse("auth-token-refresh"), {"refresh": refresh}, format="json")
        self.assertEqual(response.status_code, 401)

    def test_30_day_token_keeps_the_usual_claims(self):
        access = AccessToken(self.sign_in_with(remember_me=True)["access"])
        self.assertEqual((access["role"], access["display_name"], access["is_staff"]), ("CUSTOMER", "Rami", False))


class TemporaryPasswordTestCase(AuthAPITestCase):
    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_user("admin@example.test", PASSWORD, display_name="Lina", role=User.Role.ADMIN)
        self.customer = User.objects.create_user("rami@example.test", PASSWORD, display_name="Rami")
        self.operator = User.objects.create_user(
            "owner@example.test", PASSWORD, display_name="Owner", role=User.Role.SHOP_OPERATOR
        )

    def as_user(self, user):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(user)}")

    def set_temporary_password(self, target, by=None):
        self.as_user(by or self.admin)
        with self.assertLogs("accounts.audit", "INFO") as logs:
            response = self.client.post(reverse("admin-temporary-password", args=[target.pk]))
        self.client.credentials()
        self.assertEqual(response.status_code, 200)
        self.audit_lines = logs.output
        return response.data["temporary_password"]

    def change_password(self, user, current, new=NEW_PASSWORD):
        self.as_user(user)
        response = self.client.post(
            reverse("users-me-password"), {"current_password": current, "new_password": new}, format="json"
        )
        self.client.credentials()
        return response


class SetTemporaryPasswordTests(TemporaryPasswordTestCase):
    def test_admin_sets_a_temporary_password_that_is_shown_once_and_stored_hashed(self):
        temporary = self.set_temporary_password(self.customer)

        self.customer.refresh_from_db()
        self.assertTrue(self.customer.must_change_password)
        self.assertTrue(self.customer.check_password(temporary))
        self.assertFalse(self.customer.check_password(PASSWORD))
        self.assertNotIn(temporary, self.customer.password)
        self.assertGreaterEqual(len(temporary), 12)

    def test_response_is_never_cached(self):
        self.as_user(self.admin)
        with self.assertLogs("accounts.audit"):
            response = self.client.post(reverse("admin-temporary-password", args=[self.customer.pk]))
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_each_temporary_password_is_different(self):
        first = self.set_temporary_password(self.customer)
        second = self.set_temporary_password(self.customer)
        self.assertNotEqual(first, second)
        self.customer.refresh_from_db()
        self.assertFalse(self.customer.check_password(first))

    def test_who_for_whom_and_when_are_logged_but_never_the_password(self):
        before = timezone.now()
        temporary = self.set_temporary_password(self.operator)

        entry = TemporaryPasswordLog.objects.get()
        self.assertEqual((entry.user, entry.set_by), (self.operator, self.admin))
        self.assertEqual((entry.user_email, entry.set_by_email), ("owner@example.test", "admin@example.test"))
        self.assertGreaterEqual(entry.created_at, before)
        self.assertEqual(len(self.audit_lines), 1)
        self.assertIn(f"account {self.operator.pk} by account {self.admin.pk}", self.audit_lines[0])
        for field in TemporaryPasswordLog._meta.fields:
            self.assertNotIn(temporary, str(getattr(entry, field.attname)))
        self.assertNotIn(temporary, self.audit_lines[0])

    def test_staff_member_may_set_one_too(self):
        staff = User.objects.create_user("team@example.test", PASSWORD, display_name="Team", is_staff=True)
        self.set_temporary_password(self.customer, by=staff)
        self.assertEqual(TemporaryPasswordLog.objects.get().set_by, staff)

    def test_customers_and_shop_operators_are_refused(self):
        for caller in (self.customer, self.operator):
            with self.subTest(caller=caller.role):
                self.as_user(caller)
                response = self.client.post(reverse("admin-temporary-password", args=[self.operator.pk]))
                self.assertEqual(response.status_code, 403)
                search = self.client.get(reverse("admin-users"), {"q": "example"})
                self.assertEqual(search.status_code, 403)
        self.operator.refresh_from_db()
        self.assertTrue(self.operator.check_password(PASSWORD))
        self.assertFalse(self.operator.must_change_password)
        self.assertFalse(TemporaryPasswordLog.objects.exists())

    def test_a_forged_admin_claim_grants_nothing(self):
        token = AccessToken.for_user(self.customer)
        token["role"] = "ADMIN"
        token["is_staff"] = True
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        response = self.client.post(reverse("admin-temporary-password", args=[self.operator.pk]))
        self.assertEqual(response.status_code, 403)

    def test_needs_a_signed_in_admin(self):
        response = self.client.post(reverse("admin-temporary-password", args=[self.customer.pk]))
        self.assertEqual(response.status_code, 401)

    def test_admin_and_staff_accounts_cannot_get_one_from_the_console(self):
        other_admin = User.objects.create_user("boss@example.test", PASSWORD, display_name="Boss", role=User.Role.ADMIN)
        staff = User.objects.create_user("team@example.test", PASSWORD, display_name="Team", is_staff=True)
        self.as_user(self.admin)
        for target in (other_admin, staff, self.admin):
            with self.subTest(target=target.email):
                response = self.client.post(reverse("admin-temporary-password", args=[target.pk]))
                self.assertEqual(response.status_code, 403)
                target.refresh_from_db()
                self.assertTrue(target.check_password(PASSWORD))

    def test_unknown_account_is_404(self):
        self.as_user(self.admin)
        self.assertEqual(self.client.post(reverse("admin-temporary-password", args=[999999])).status_code, 404)

    def test_sign_ins_held_before_the_reset_stop_working(self):
        old_refresh = self.sign_in("rami@example.test")
        self.assertEqual(old_refresh.status_code, 200)
        self.set_temporary_password(self.customer)
        response = self.client.post(reverse("auth-token-refresh"), {"refresh": old_refresh.data["refresh"]}, format="json")
        self.assertEqual(response.status_code, 401)


class FindAccountTests(TemporaryPasswordTestCase):
    def test_admin_finds_accounts_by_email_or_number(self):
        self.as_user(self.admin)
        by_email = self.client.get(reverse("admin-users"), {"q": "RAMI@"}).data["users"]
        self.assertEqual([u["email"] for u in by_email], ["rami@example.test"])
        self.assertTrue(by_email[0]["can_get_temporary_password"])
        self.assertNotIn("password", by_email[0])

        by_number = self.client.get(reverse("admin-users"), {"q": f"#{self.operator.pk}"}).data["users"]
        self.assertIn(self.operator.pk, [u["id"] for u in by_number])

        admins = self.client.get(reverse("admin-users"), {"q": "admin@"}).data["users"]
        self.assertFalse(admins[0]["can_get_temporary_password"])

    def test_empty_search_returns_nothing(self):
        self.as_user(self.admin)
        self.assertEqual(self.client.get(reverse("admin-users")).data, {"users": []})


class ForcedPasswordChangeTests(TemporaryPasswordTestCase):
    def test_user_logs_in_with_the_temporary_password_and_is_told_to_change_it(self):
        temporary = self.set_temporary_password(self.customer)
        response = self.sign_in("rami@example.test", temporary)
        self.assertEqual(response.status_code, 200)

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")
        me = self.client.get(reverse("users-me")).data
        self.assertTrue(me["must_change_password"])

    def test_after_the_change_the_temporary_password_stops_working(self):
        temporary = self.set_temporary_password(self.customer)

        response = self.change_password(self.customer, temporary)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["must_change_password"])
        self.customer.refresh_from_db()
        self.assertFalse(self.customer.must_change_password)
        self.assertEqual(self.sign_in("rami@example.test", temporary).status_code, 401)
        self.assertEqual(self.sign_in("rami@example.test", NEW_PASSWORD).status_code, 200)

    def test_new_password_must_pass_django_password_validation(self):
        temporary = self.set_temporary_password(self.customer)
        for weak in ("12345678", "password", "short", "rami@example.test"):
            with self.subTest(weak=weak):
                response = self.change_password(self.customer, temporary, weak)
                self.assertEqual(response.status_code, 400)
                self.assertIn("new_password", response.data)
        self.customer.refresh_from_db()
        self.assertTrue(self.customer.must_change_password)
        self.assertTrue(self.customer.check_password(temporary))

    def test_new_password_cannot_be_the_temporary_one(self):
        temporary = self.set_temporary_password(self.customer)
        response = self.change_password(self.customer, temporary, temporary)
        self.assertEqual(response.status_code, 400)
        self.customer.refresh_from_db()
        self.assertTrue(self.customer.must_change_password)

    def test_wrong_current_password_is_refused(self):
        self.set_temporary_password(self.customer)
        response = self.change_password(self.customer, "Not-the-temporary-1")
        self.assertEqual(response.status_code, 400)
        self.assertIn("current_password", response.data)
        self.customer.refresh_from_db()
        self.assertTrue(self.customer.must_change_password)

    def test_needs_a_signed_in_user(self):
        response = self.client.post(
            reverse("users-me-password"), {"current_password": PASSWORD, "new_password": NEW_PASSWORD}, format="json"
        )
        self.assertEqual(response.status_code, 401)
