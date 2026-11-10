"""LP-18: the customer's side of the app: bottom bar, profile, log out and account rules."""

import base64
import json
import time
from types import SimpleNamespace
from urllib.parse import urlencode

from django.test import Client, SimpleTestCase
from django.urls import reverse

from . import tokens
from .services import ServiceUnavailable
from .test_accounts import PASSWORD, TOKENS, patch_service
from .test_lp62_cart import CartTestCase
from .test_lp56_login_signup import CUSTOMER, OPERATOR

ADMIN = {**CUSTOMER, "id": 99, "email": "admin@example.test", "display_name": "Lina", "role": "ADMIN"}


def refresh_token_at(expires_at):
    """A token shaped like the one Accounts issues (header.payload.signature)
    that runs out at the given Unix time."""

    def part(data):
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()

    payload = {"token_type": "refresh", "exp": expires_at}
    return ".".join((part({"alg": "RS256", "typ": "JWT"}), part(payload), "signature"))


def refresh_token(seconds_from_now):
    return refresh_token_at(int(time.time()) + seconds_from_now)


class CustomerBottomBarTests(CartTestCase):
    def labels(self, response):
        html = response.content.decode()
        start = html.index('class="bottom-bar-customer"')
        bar = html[start:html.index("</nav>", start)]
        return bar

    def test_customer_sees_the_four_links_on_each_customer_page(self):
        self.sign_in_as(CUSTOMER)
        for name in ("my-account", "search", "my-orders", "cart"):
            with self.subTest(page=name):
                bar = self.labels(self.client.get(reverse(name)))
                for label in ("Home", "Search", "My orders", "Profile"):
                    self.assertIn(f">{label}</a>", bar)

    def test_current_page_is_highlighted_and_only_that_one(self):
        self.sign_in_as(CUSTOMER)
        for name, label in (("my-account", "Profile"), ("search", "Search"), ("my-orders", "My orders")):
            with self.subTest(page=name):
                bar = self.labels(self.client.get(reverse(name)))
                self.assertEqual(bar.count('aria-current="page"'), 1)
                self.assertIn(f'aria-current="page">{label}</a>', bar)

    def test_restaurants_do_not_get_the_customer_bar(self):
        self.sign_in_as(OPERATOR)
        response = self.client.get(reverse("workspace"))
        self.assertNotContains(response, "bottom-bar-customer")

    def test_placeholder_pages_are_customers_only(self):
        for name in ("search", "my-orders"):
            with self.subTest(page=name, who="signed out"):
                response = self.client.get(reverse(name))
                self.assertRedirects(response, f"{reverse('login')}?next={reverse(name)}", fetch_redirect_response=False)
        self.sign_in_as(OPERATOR)
        for name in ("search", "my-orders"):
            with self.subTest(page=name, who="restaurant"):
                self.assertRedirects(self.client.get(reverse(name)), reverse("workspace"), fetch_redirect_response=False)


class ProfilePageTests(CartTestCase):
    url = reverse("my-account")

    def setUp(self):
        self.sign_in_as(CUSTOMER)

    def test_shows_name_email_the_name_form_and_log_out(self):
        response = self.client.get(self.url)

        self.assertContains(response, "rami@example.test")
        self.assertContains(response, 'name="display_name"')
        self.assertContains(response, 'value="Rami"')
        self.assertContains(response, f'action="{reverse("sign-out")}"')

    def test_saving_a_new_name_updates_the_page_and_the_session(self):
        with patch_service("update_account", return_value=(200, {**CUSTOMER, "display_name": "Rami K."})) as call:
            response = self.client.post(self.url, {"display_name": "Rami K."})

        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        self.assertEqual(call.call_args.args[1], "Rami K.")
        self.assertEqual(self.client.session["account"]["display_name"], "Rami K.")
        self.assertContains(self.client.get(self.url), "Rami K.")

    def test_a_blank_name_is_refused_without_calling_accounts(self):
        with patch_service("update_account") as call:
            response = self.client.post(self.url, {"display_name": "   "})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This field is required")
        call.assert_not_called()
        self.assertEqual(self.client.session["account"]["display_name"], "Rami")

    def test_accounts_being_down_shows_a_friendly_message_and_keeps_the_old_name(self):
        with patch_service("update_account", side_effect=ServiceUnavailable):
            response = self.client.post(self.url, {"display_name": "Rami K."})

        self.assertContains(response, "your name was not saved")
        self.assertEqual(self.client.session["account"]["display_name"], "Rami")

    def test_accounts_rejecting_the_name_shows_its_message(self):
        with patch_service("update_account", return_value=(400, {"display_name": ["Too long."]})):
            response = self.client.post(self.url, {"display_name": "Rami"})

        self.assertContains(response, "Too long.")

    def test_log_out_ends_the_session_and_returns_to_login(self):
        with patch_service("sign_out", return_value=(200, {})):
            response = self.client.post(reverse("sign-out"))

        self.assertRedirects(response, reverse("login"), fetch_redirect_response=False)
        self.assertNotIn("account", self.client.session)
        self.assertRedirects(
            self.client.get(self.url), f"{reverse('login')}?next={self.url}", fetch_redirect_response=False
        )

    def test_signed_out_visitors_and_restaurants_cannot_open_it(self):
        self.assertRedirects(
            Client().get(self.url), f"{reverse('login')}?next={self.url}", fetch_redirect_response=False
        )
        self.sign_in_as(OPERATOR)
        self.assertRedirects(self.client.get(self.url), reverse("workspace"), fetch_redirect_response=False)


class CustomersOnlyTests(CartTestCase):
    """A restaurant or the administrator cannot use customer-only pages or
    actions, even by sending the request by hand."""

    def test_forged_requests_from_restaurants_and_admins_do_nothing(self):
        for account, landing in ((OPERATOR, "workspace"), (ADMIN, "console")):
            with self.subTest(role=account["role"]):
                self.sign_in_as(account)
                with patch_service("listing_offer") as food, patch_service("update_account") as rename:
                    responses = [
                        self.client.post(reverse("cart-add", args=[42]), {"start_new": "1"}),
                        self.client.post(reverse("cart-update", args=[42]), {"action": "increase"}),
                        self.client.get(reverse("cart")),
                        self.client.post(reverse("my-account"), {"display_name": "Hacked"}),
                        self.client.get(reverse("search")),
                        self.client.get(reverse("my-orders")),
                    ]

                for response in responses:
                    self.assertRedirects(response, reverse(landing), fetch_redirect_response=False)
                food.assert_not_called()
                rename.assert_not_called()
                self.assertEqual(self.lines(), {})
                self.assertEqual(self.client.session["account"]["display_name"], account["display_name"])

    def test_restaurants_and_admins_do_not_get_the_customer_bar(self):
        self.sign_in_as(ADMIN)
        with patch_service("browse_listings", return_value=(200, [])):
            response = self.client.get(reverse("browse-offers"))
        self.assertNotContains(response, "bottom-bar-customer")


class SessionExpiryTests(CartTestCase):
    """When the sign-in has run out, a protected customer action sends the
    customer to /login and back to the same page, and changes nothing."""

    profile = reverse("my-account")

    def setUp(self):
        self.sign_in_as(CUSTOMER)

    def set_refresh_token(self, token):
        session = self.client.session
        session[tokens.REFRESH_TOKEN_KEY] = token
        session.save()

    def login_for(self, path):
        return f"{reverse('login')}?{urlencode({'next': path})}"

    def assert_session_ended(self, response, path):
        self.assertRedirects(response, self.login_for(path), fetch_redirect_response=False)
        self.assertNotIn("account", self.client.session)
        self.assertNotIn(tokens.REFRESH_TOKEN_KEY, self.client.session)

    def test_expired_profile_goes_to_login_and_back_to_the_profile(self):
        self.set_refresh_token(refresh_token(-60))

        with patch_service("update_account") as rename:
            response = self.client.get(self.profile)

        self.assert_session_ended(response, self.profile)
        rename.assert_not_called()
        self.assertContains(self.client.get(response.url), "Your session has ended")

        with patch_service("sign_in", return_value=(200, TOKENS)), \
                patch_service("account", return_value=(200, CUSTOMER)):
            back = self.client.post(response.url, {"email": CUSTOMER["email"], "password": PASSWORD})
        self.assertRedirects(back, self.profile, fetch_redirect_response=False)

    def test_expired_session_does_not_save_a_new_name(self):
        self.set_refresh_token(refresh_token(-1))

        with patch_service("update_account") as rename:
            response = self.client.post(self.profile, {"display_name": "Rami K."})

        self.assert_session_ended(response, self.profile)
        rename.assert_not_called()

    def test_expired_session_on_the_cart_page(self):
        self.set_refresh_token(refresh_token(-1))

        with patch_service("listing_offer") as food:
            response = self.client.get(reverse("cart"))

        self.assert_session_ended(response, reverse("cart"))
        food.assert_not_called()

    def test_expired_session_adds_nothing_and_comes_back_to_the_food(self):
        url = f"{reverse('cart-add', args=[42])}?{urlencode({'back': self.back})}"
        self.set_refresh_token(refresh_token(-1))

        with patch_service("listing_offer") as food:
            response = self.client.post(url)

        self.assert_session_ended(response, url)
        food.assert_not_called()  # nothing was checked, reserved or counted
        self.assertEqual(self.lines(), {})

        # After logging in the browser opens the same address with GET.
        self.sign_in_as(CUSTOMER)
        self.assertRedirects(self.client.get(url), self.back, fetch_redirect_response=False)
        self.assertEqual(self.lines(), {})

    def test_expired_session_on_a_cart_button_comes_back_to_the_cart(self):
        self.add(42)
        url = reverse("cart-update", args=[42])
        self.set_refresh_token(refresh_token(-1))

        with patch_service("listing_offer") as food:
            response = self.client.post(url, {"action": "increase"})

        self.assert_session_ended(response, url)
        food.assert_not_called()

        # GET after logging in again: nothing changes and they land on the cart.
        self.sign_in_as(CUSTOMER)
        self.assertRedirects(self.client.get(url), reverse("cart"), fetch_redirect_response=False)
        self.assertEqual(self.lines(), {})

    def test_a_refresh_token_rejected_by_accounts_also_returns_to_the_same_page(self):
        with patch_service("update_account", return_value=(401, {})), \
                patch_service("refresh", return_value=(401, {})):
            response = self.client.post(self.profile, {"display_name": "Rami K."})

        self.assert_session_ended(response, self.profile)

    def test_live_and_unreadable_tokens_keep_the_session(self):
        for token in (refresh_token(3600), "refresh-token-1"):
            with self.subTest(token=token[:12]):
                self.set_refresh_token(token)
                self.assertEqual(self.client.get(self.profile).status_code, 200)
                self.assertEqual(self.client.session["account"]["email"], CUSTOMER["email"])


class RefreshTokenExpiryTests(SimpleTestCase):
    def request_with(self, token):
        return SimpleNamespace(session={tokens.REFRESH_TOKEN_KEY: token} if token is not None else {})

    def test_reads_the_expiry_time(self):
        self.assertTrue(tokens.refresh_token_expired(self.request_with(refresh_token(-5))))
        self.assertFalse(tokens.refresh_token_expired(self.request_with(refresh_token(300))))

    def test_expires_exactly_at_the_expiry_time(self):
        expiry = 1_900_000_000
        request = self.request_with(refresh_token_at(expiry))
        self.assertFalse(tokens.refresh_token_expired(request, now=expiry - 1))
        self.assertTrue(tokens.refresh_token_expired(request, now=expiry))

    def test_anything_unreadable_counts_as_not_expired(self):
        for token in (None, "", "refresh-token-1", "a.b.c", "a..c", "a.!!!!.c", ".e30.", "a.bnVsbA.c"):
            with self.subTest(token=token):
                self.assertFalse(tokens.refresh_token_expired(self.request_with(token)))


class EmailRuleOnTheProfileTests(CartTestCase):
    """One email, one account. Customers can't change their email here, so a
    clash cannot start from this page; if Accounts ever reports one it is
    shown as a plain message."""

    url = reverse("my-account")

    def setUp(self):
        self.sign_in_as(CUSTOMER)

    def test_the_email_is_shown_but_cannot_be_edited(self):
        response = self.client.get(self.url)

        self.assertContains(response, CUSTOMER["email"])
        self.assertNotContains(response, 'name="email"')

    def test_a_forged_email_field_is_never_sent_to_accounts(self):
        with patch_service("update_account", return_value=(200, {**CUSTOMER, "display_name": "Rami K."})) as call:
            self.client.post(self.url, {"display_name": "Rami K.", "email": "someone-else@example.test"})

        self.assertEqual(call.call_args.args[1:], ("Rami K.",))
        self.assertEqual(self.client.session["account"]["email"], CUSTOMER["email"])

    def test_an_email_clash_reported_by_accounts_shows_a_friendly_message(self):
        clash = {"email": ["An account with this email already exists."]}
        with patch_service("update_account", return_value=(400, clash)):
            response = self.client.post(self.url, {"display_name": "Rami K."})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "An account with this email already exists.")
        self.assertEqual(self.client.session["account"]["display_name"], "Rami")
        self.assertEqual(self.client.session["account"]["email"], CUSTOMER["email"])