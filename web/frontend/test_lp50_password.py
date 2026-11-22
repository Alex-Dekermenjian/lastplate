"""LP-50: "Keep me logged in" for 30 days, "Forgot your password?", the
admin's temporary passwords in /console, and the forced "Choose a new
password" page."""

from contextlib import ExitStack
from unittest import mock

from django.conf import settings
from django.contrib.sessions.models import Session
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape

from . import account_views, password_views, services, tokens
from .services import ServiceUnavailable
from .test_accounts import PASSWORD, TOKENS, WebAuthTestCase, patch_service
from .test_lp56_login_signup import CUSTOMER, OPERATOR, signup_data
from .test_lp57_console import ADMIN

THIRTY_DAYS = 60 * 60 * 24 * 30
ONE_DAY = 60 * 60 * 24
TEMPORARY = "Hk7m-Pq2x-Rt9b"
NEW_PASSWORD = "Fresh-start!731Harbour"
MUST_CHANGE = {**CUSTOMER, "must_change_password": True}


class NoServiceCalls:
    """Every request to every service fails the test."""

    def __enter__(self):
        self.stack = ExitStack()
        self.calls = [self.stack.enter_context(mock.patch.object(services, name))
                      for name in ("_call", "_call_multipart")]
        return self

    def __exit__(self, *exc):
        self.stack.close()

    @property
    def called(self):
        return any(call.called for call in self.calls)


def log_in(client, account, remember=True, password=PASSWORD):
    data = {"email": account["email"], "password": password}
    if remember:
        data["remember_me"] = "on"
    with patch_service("sign_in", return_value=(200, TOKENS)) as sign_in, \
            patch_service("account", return_value=(200, account)):
        response = client.post(reverse("login"), data)
    return response, sign_in


def days_left(client):
    row = Session.objects.get(session_key=client.session.session_key)
    return (row.expire_date - timezone.now()).total_seconds() / ONE_DAY


class KeepMeLoggedInTests(WebAuthTestCase):
    def test_login_page_offers_30_days_and_a_forgot_password_link(self):
        response = self.client.get(reverse("login"))
        self.assertContains(response, "Keep me logged in (30 days)")
        self.assertContains(response, f'href="{reverse("forgot-password")}"')
        self.assertContains(response, "Forgot your password?")

    def test_ticked_box_asks_for_a_30_day_token_and_keeps_the_session_30_days(self):
        _, sign_in = log_in(self.client, CUSTOMER, remember=True)

        sign_in.assert_called_once_with(CUSTOMER["email"], PASSWORD, remember_me=True)
        self.assertFalse(self.client.session.get_expire_at_browser_close())
        self.assertEqual(self.client.session.get_expiry_age(), THIRTY_DAYS)
        self.assertGreater(days_left(self.client), 29.9)

    def test_unticked_box_keeps_the_old_lifetimes(self):
        _, sign_in = log_in(self.client, CUSTOMER, remember=False)

        sign_in.assert_called_once_with(CUSTOMER["email"], PASSWORD, remember_me=False)
        # As before LP-50: ends when the browser closes, and after 1 day at the latest.
        self.assertTrue(self.client.session.get_expire_at_browser_close())
        self.assertEqual(settings.SESSION_COOKIE_AGE, ONE_DAY)
        self.assertLessEqual(days_left(self.client), 1)

    def test_only_a_ticked_box_sends_remember_me_to_accounts(self):
        with mock.patch.object(services, "_call", return_value=(200, TOKENS)) as call:
            services.sign_in("rami@example.test", PASSWORD)
            services.sign_in("rami@example.test", PASSWORD, remember_me=True)
        self.assertNotIn("remember_me", call.call_args_list[0].args[3])
        self.assertIs(call.call_args_list[1].args[3]["remember_me"], True)

    def test_sign_up_session_is_unchanged(self):
        with patch_service("register", return_value=(201, {**TOKENS, "user": CUSTOMER})):
            self.client.post(reverse("signup"), signup_data())
        self.assertFalse(self.client.session.get_expire_at_browser_close())
        self.assertEqual(self.client.session.get_expiry_age(), ONE_DAY)


class ForgotPasswordPageTests(WebAuthTestCase):
    def test_explains_how_to_get_a_temporary_password(self):
        with NoServiceCalls() as calls:
            response = self.client.get(reverse("forgot-password"))
        self.assertContains(
            response,
            "Contact LastPlate support; after checking who you are, an admin will give you a temporary password.",
        )
        self.assertFalse(calls.called)


class ForcedNewPasswordTests(WebAuthTestCase):
    def log_in_with_temporary_password(self, next_url=None):
        data = {"email": MUST_CHANGE["email"], "password": TEMPORARY, "remember_me": "on"}
        url = reverse("login") + (f"?next={next_url}" if next_url else "")
        with patch_service("sign_in", return_value=(200, TOKENS)), \
                patch_service("account", return_value=(200, MUST_CHANGE)):
            return self.client.post(url, data)

    def choose(self, current=TEMPORARY, new=NEW_PASSWORD, again=None):
        return self.client.post(reverse("choose-new-password"), {
            "current_password": current, "new_password": new, "new_password_again": again or new,
        })

    def test_login_with_a_temporary_password_goes_straight_to_choose_a_new_password(self):
        response = self.log_in_with_temporary_password(next_url="/cart/")
        self.assertRedirects(response, reverse("choose-new-password"), fetch_redirect_response=False)

        page = self.client.get(reverse("choose-new-password"))
        self.assertContains(page, "Choose a new password")
        self.assertContains(page, 'name="new_password"')

    def test_every_other_page_is_blocked_until_the_password_is_changed(self):
        self.log_in_with_temporary_password()
        pages = [
            ("get", reverse("home")), ("get", reverse("my-account")), ("post", reverse("my-account")),
            ("get", reverse("cart")), ("get", reverse("my-orders")), ("get", reverse("browse-offers")),
            ("get", reverse("workspace")), ("get", reverse("console")), ("get", reverse("console-accounts")),
            ("get", reverse("login")), ("get", reverse("signup")),
        ]
        for method, url in pages:
            with self.subTest(method=method, url=url), NoServiceCalls() as calls:
                response = getattr(self.client, method)(url, {"display_name": "X"} if method == "post" else None)
                self.assertRedirects(response, reverse("choose-new-password"), fetch_redirect_response=False)
                self.assertFalse(calls.called)

    def test_sign_out_still_works(self):
        self.log_in_with_temporary_password()
        with patch_service("sign_out", return_value=(200, {})):
            response = self.client.post(reverse("sign-out"))
        self.assertRedirects(response, reverse("login"), fetch_redirect_response=False)
        self.assertNotIn(account_views.NEW_PASSWORD_KEY, self.client.session)

    def test_after_the_change_the_account_is_unlocked(self):
        self.log_in_with_temporary_password()
        changed = {**CUSTOMER, "must_change_password": False}
        with patch_service("change_password", return_value=(200, changed)) as change:
            response = self.choose()

        change.assert_called_once_with(TOKENS["access"], TEMPORARY, NEW_PASSWORD)
        self.assertRedirects(response, reverse("home"), fetch_redirect_response=False)
        self.assertNotIn(account_views.NEW_PASSWORD_KEY, self.client.session)
        self.assertEqual(self.client.get(reverse("my-account")).status_code, 200)

    def test_accounts_password_rules_are_shown_and_the_lock_stays(self):
        self.log_in_with_temporary_password()
        with patch_service("change_password", return_value=(400, {"new_password": ["This password is too common."]})):
            response = self.choose(new="password1")
        self.assertContains(response, "This password is too common.")
        self.assertTrue(self.client.session[account_views.NEW_PASSWORD_KEY])
        self.assert_no_secrets(response, TEMPORARY)

    def test_wrong_temporary_password_is_shown_on_its_field(self):
        self.log_in_with_temporary_password()
        refusal = {"current_password": ["Your current password is incorrect."]}
        with patch_service("change_password", return_value=(400, refusal)):
            response = self.choose(current="nope")
        self.assertContains(response, "Your current password is incorrect.")
        self.assertTrue(self.client.session[account_views.NEW_PASSWORD_KEY])

    def test_new_passwords_that_dont_match_are_not_sent(self):
        self.log_in_with_temporary_password()
        with NoServiceCalls() as calls:
            response = self.choose(again=NEW_PASSWORD + "x")
        self.assertContains(response, "The two new passwords don")
        self.assertFalse(calls.called)

    def test_account_service_down_keeps_the_lock(self):
        self.log_in_with_temporary_password()
        with patch_service("change_password", side_effect=ServiceUnavailable("accounts")):
            response = self.choose()
        self.assertContains(response, escape(password_views.NOT_SAVED))
        self.assertTrue(self.client.session[account_views.NEW_PASSWORD_KEY])

    def test_page_is_not_for_accounts_without_a_temporary_password(self):
        log_in(self.client, CUSTOMER)
        response = self.client.get(reverse("choose-new-password"))
        self.assertRedirects(response, reverse("home"), fetch_redirect_response=False)
        self.client = self.client_class()
        response = self.client.get(reverse("choose-new-password"))
        self.assertRedirects(response, reverse("login"), fetch_redirect_response=False)


class ConsoleTemporaryPasswordTests(WebAuthTestCase):
    FOUND = {"users": [{"id": 8, "email": "rami@example.test", "display_name": "Rami", "role": "CUSTOMER",
                        "must_change_password": False, "can_get_temporary_password": True}]}

    def sign_in_as(self, account):
        session = self.client.session
        session[tokens.ACCESS_TOKEN_KEY] = TOKENS["access"]
        session[tokens.REFRESH_TOKEN_KEY] = TOKENS["refresh"]
        session[account_views.ACCOUNT_KEY] = {k: account[k] for k in ("id", "email", "display_name", "role")}
        session.save()

    def test_admin_finds_an_account(self):
        self.sign_in_as(ADMIN)
        with patch_service("admin_find_accounts", return_value=(200, self.FOUND)) as find:
            response = self.client.get(reverse("console-accounts"), {"q": "rami"})
        find.assert_called_once_with(TOKENS["access"], "rami")
        self.assertContains(response, "rami@example.test")
        self.assertContains(response, reverse("console-temporary-password", args=[8]))
        self.assertContains(response, f'href="{reverse("console-accounts")}"')  # the console tab

    def test_temporary_password_is_shown_once_and_never_kept(self):
        self.sign_in_as(ADMIN)
        answer = {"user": self.FOUND["users"][0], "temporary_password": TEMPORARY, "set_at": "2026-11-22T10:00:00Z"}
        with patch_service("admin_set_temporary_password", return_value=(200, answer)) as set_password:
            response = self.client.post(reverse("console-temporary-password", args=[8]))

        set_password.assert_called_once_with(TOKENS["access"], 8)
        self.assertContains(response, TEMPORARY)
        self.assertContains(response, "only time it is shown")
        self.assertIn("no-store", response["Cache-Control"])
        session_row = Session.objects.get(session_key=self.client.session.session_key)
        self.assertNotIn(TEMPORARY, str(session_row.get_decoded()))

        with patch_service("admin_find_accounts", return_value=(200, self.FOUND)):
            page = self.client.get(reverse("console-accounts"), {"q": "rami"})
        self.assertNotContains(page, TEMPORARY)

    def test_setting_needs_a_post(self):
        self.sign_in_as(ADMIN)
        with NoServiceCalls() as calls:
            response = self.client.get(reverse("console-temporary-password", args=[8]))
        self.assertEqual(response.status_code, 405)
        self.assertFalse(calls.called)

    def test_customers_and_restaurant_owners_are_refused(self):
        for account in (CUSTOMER, OPERATOR):
            with self.subTest(account["role"]):
                self.client = self.client_class()
                self.sign_in_as(account)
                with NoServiceCalls() as calls:
                    search = self.client.get(reverse("console-accounts"), {"q": "rami"})
                    action = self.client.post(reverse("console-temporary-password", args=[8]))
                self.assertEqual(search.status_code, 302)
                self.assertEqual(action.status_code, 302)
                self.assertNotIn("console", action["Location"])
                self.assertFalse(calls.called)

    def test_anonymous_visitors_are_sent_to_log_in(self):
        with NoServiceCalls() as calls:
            response = self.client.post(reverse("console-temporary-password", args=[8]))
        self.assertTrue(response["Location"].startswith(reverse("login")))
        self.assertFalse(calls.called)

    def test_accounts_refusal_is_shown(self):
        self.sign_in_as(ADMIN)
        refusal = {"code": "not_allowed", "detail": "Temporary passwords are only for customer and restaurant accounts."}
        with patch_service("admin_set_temporary_password", return_value=(403, refusal)), \
                patch_service("admin_find_accounts", return_value=(200, {"users": []})):
            response = self.client.post(reverse("console-temporary-password", args=[99]), follow=True)
        self.assertContains(response, refusal["detail"])

    def test_account_service_down_says_the_outcome_is_unknown(self):
        self.sign_in_as(ADMIN)
        with patch_service("admin_set_temporary_password", side_effect=ServiceUnavailable("accounts")):
            response = self.client.post(reverse("console-temporary-password", args=[8]), follow=True)
        self.assertContains(response, "tell whether a temporary password was set")
