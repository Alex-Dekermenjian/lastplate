from unittest import mock

from django.conf import settings
from django.contrib.sessions.models import Session
from django.test import Client, TestCase
from django.urls import reverse
from django.utils.html import escape

from . import account_views, services, tokens
from .services import ServiceUnavailable

PASSWORD = "Local-test-only!582Orbit"
ACCOUNT = {"id": 7, "email": "owner@example.test", "display_name": "Owner", "role": "SHOP_OPERATOR"}
TOKENS = {"access": "access-token-1", "refresh": "refresh-token-1"}
SHOP = {"id": 3, "operator_user_id": 7, "name": "Cedar Crumb Bakery", "approval_status": "PENDING",
        "created_at": "2026-11-03T18:00:00Z", "updated_at": "2026-11-03T18:00:00Z"}


def registration(**overrides):
    data = {"display_name": "Owner", "shop_name": "Cedar Crumb Bakery", "email": "owner@example.test",
            "password": PASSWORD, "password_confirm": PASSWORD}
    data.update(overrides)
    return data


def patch_service(name, **kwargs):
    return mock.patch.object(services, name, **kwargs)


class WebAuthTestCase(TestCase):
    def sign_in_session(self, access="access-token-1", refresh="refresh-token-1"):
        session = self.client.session
        session[tokens.ACCESS_TOKEN_KEY] = access
        session[tokens.REFRESH_TOKEN_KEY] = refresh
        session[account_views.ACCOUNT_KEY] = ACCOUNT
        session.save()
        return session.session_key

    def assert_no_secrets(self, response, *secrets):
        content = response.content.decode()
        for secret in (PASSWORD, *TOKENS.values(), *secrets):
            self.assertNotIn(secret, content)


class RegistrationTests(WebAuthTestCase):
    url = reverse("register")

    def test_form_has_csrf_token_and_all_fields(self):
        response = self.client.get(self.url)
        self.assertContains(response, "csrfmiddlewaretoken")
        for name in ("display_name", "shop_name", "email", "password", "password_confirm"):
            self.assertContains(response, f'name="{name}"')

    def test_post_without_csrf_token_is_rejected(self):
        client = Client(enforce_csrf_checks=True)
        with patch_service("register") as register:
            self.assertEqual(client.post(self.url, registration()).status_code, 403)
        register.assert_not_called()

    def test_success_creates_pending_shop_and_shows_workspace(self):
        old_key = self.client.session.session_key
        with patch_service("register", return_value=(201, {**TOKENS, "user": ACCOUNT})) as register, \
                patch_service("create_shop", return_value=(201, SHOP)) as create_shop:
            response = self.client.post(self.url, registration())

        self.assertRedirects(response, reverse("workspace"), fetch_redirect_response=False)
        register.assert_called_once_with("Owner", "owner@example.test", PASSWORD)
        create_shop.assert_called_once_with("access-token-1", "Cedar Crumb Bakery")
        session = self.client.session
        self.assertNotEqual(session.session_key, old_key)
        self.assertEqual(session[tokens.REFRESH_TOKEN_KEY], "refresh-token-1")
        self.assertNotIn(account_views.PENDING_SHOP_KEY, session)
        cookie = self.client.cookies[settings.SESSION_COOKIE_NAME].value
        self.assertNotIn("token", cookie)

        with patch_service("my_shop", return_value=(200, SHOP)):
            workspace = self.client.get(reverse("workspace"))
        self.assertContains(workspace, "Cedar Crumb Bakery")
        self.assertContains(workspace, "Pending review")
        self.assert_no_secrets(workspace)

    def test_local_validation_keeps_safe_input_but_never_passwords(self):
        with patch_service("register") as register:
            response = self.client.post(self.url, registration(password_confirm="Different-password-91"))
        register.assert_not_called()
        self.assertContains(response, "The passwords do not match.")
        self.assertContains(response, 'value="owner@example.test"')
        self.assertContains(response, 'value="Cedar Crumb Bakery"')
        self.assertNotContains(response, PASSWORD)
        self.assertNotContains(response, "Different-password-91")

    def test_accounts_validation_errors_are_shown_on_fields(self):
        errors = {"email": ["An account with this email already exists."], "password": ["This password is too common."]}
        with patch_service("register", return_value=(400, errors)), patch_service("create_shop") as create_shop:
            response = self.client.post(self.url, registration())
        create_shop.assert_not_called()
        self.assertContains(response, "An account with this email already exists.")
        self.assertContains(response, "This password is too common.")
        self.assertNotContains(response, PASSWORD)
        self.assertNotIn(tokens.REFRESH_TOKEN_KEY, self.client.session)

    def test_throttled_registration_shows_wait_message(self):
        with patch_service("register", return_value=(429, {"detail": "throttled"})):
            response = self.client.post(self.url, registration())
        self.assertContains(response, account_views.TOO_MANY_REGISTRATIONS)

    def test_timeout_is_recovered_only_by_signing_in_with_the_same_details(self):
        with patch_service("register", side_effect=ServiceUnavailable("accounts")), \
                patch_service("sign_in", return_value=(200, TOKENS)) as sign_in, \
                patch_service("account", return_value=(200, ACCOUNT)), \
                patch_service("create_shop", return_value=(201, SHOP)):
            response = self.client.post(self.url, registration())
        sign_in.assert_called_once_with("owner@example.test", PASSWORD)
        self.assertRedirects(response, reverse("workspace"), fetch_redirect_response=False)
        self.assertEqual(self.client.session[account_views.ACCOUNT_KEY]["id"], 7)

    def test_timeout_then_failed_sign_in_does_not_continue(self):
        with patch_service("register", side_effect=ServiceUnavailable("accounts")), \
                patch_service("sign_in", return_value=(401, {"detail": "Email or password is incorrect."})), \
                patch_service("create_shop") as create_shop:
            response = self.client.post(self.url, registration())
        create_shop.assert_not_called()
        self.assertContains(response, escape(account_views.REGISTRATION_FAILED))
        self.assertNotContains(response, "already exists")
        self.assertNotIn(tokens.REFRESH_TOKEN_KEY, self.client.session)

    def test_timeout_with_accounts_still_down_says_outcome_is_unknown(self):
        with patch_service("register", side_effect=ServiceUnavailable("accounts")), \
                patch_service("sign_in", side_effect=ServiceUnavailable("accounts")):
            response = self.client.post(self.url, registration())
        self.assertContains(response, "can&#x27;t tell whether your account was created")
        self.assertNotIn(tokens.REFRESH_TOKEN_KEY, self.client.session)

    def test_shop_failure_keeps_account_and_retry_finishes_setup(self):
        with patch_service("register", return_value=(201, {**TOKENS, "user": ACCOUNT})), \
                patch_service("create_shop", side_effect=ServiceUnavailable("marketplace")):
            response = self.client.post(self.url, registration())
        self.assertRedirects(response, reverse("finish-setup"), fetch_redirect_response=False)
        self.assertEqual(self.client.session[account_views.ACCOUNT_KEY]["id"], 7)
        self.assertEqual(self.client.session[account_views.PENDING_SHOP_KEY], "Cedar Crumb Bakery")

        with patch_service("my_shop", return_value=(404, {})):
            page = self.client.get(reverse("finish-setup"))
        self.assertContains(page, "couldn&#x27;t set up your shop yet")
        self.assertContains(page, 'value="Cedar Crumb Bakery"')

        # The retry calls Marketplace's idempotent endpoint; a 200 means the shop already existed.
        with patch_service("create_shop", return_value=(200, SHOP)) as create_shop:
            done = self.client.post(reverse("finish-setup"), {"shop_name": "Cedar Crumb Bakery"})
        create_shop.assert_called_once_with("access-token-1", "Cedar Crumb Bakery")
        self.assertRedirects(done, reverse("workspace"), fetch_redirect_response=False)
        self.assertNotIn(account_views.PENDING_SHOP_KEY, self.client.session)

    def test_finish_setup_retry_failure_is_explained(self):
        self.sign_in_session()
        with patch_service("create_shop", side_effect=ServiceUnavailable("marketplace")):
            response = self.client.post(reverse("finish-setup"), {"shop_name": "Cedar Crumb Bakery"})
        self.assertContains(response, "Your account is safe")


class SignInTests(WebAuthTestCase):
    url = reverse("sign-in")

    def post(self, data=None, url=None, **service_returns):
        defaults = {"sign_in": {"return_value": (200, TOKENS)}, "account": {"return_value": (200, ACCOUNT)}}
        defaults.update(service_returns)
        with patch_service("sign_in", **defaults["sign_in"]), patch_service("account", **defaults["account"]), \
                patch_service("sign_out", return_value=(200, {})) as sign_out:
            response = self.client.post(url or self.url, data or {"email": "owner@example.test", "password": PASSWORD})
        return response, sign_out

    def test_success_renews_session_id_and_redirects_to_workspace(self):
        self.client.get(self.url)
        old_key = self.client.session.session_key
        response, _ = self.post()
        self.assertRedirects(response, reverse("workspace"), fetch_redirect_response=False)
        self.assertNotEqual(self.client.session.session_key, old_key)
        self.assertEqual(self.client.session[tokens.ACCESS_TOKEN_KEY], "access-token-1")

    def test_safe_next_is_followed_and_external_next_is_ignored(self):
        response, _ = self.post(url=f"{self.url}?next=/workspace/finish-setup/")
        self.assertRedirects(response, "/workspace/finish-setup/", fetch_redirect_response=False)
        for target in ("https://evil.example/", "//evil.example/", "/\\evil.example"):
            with self.subTest(target=target):
                self.client = Client()
                response, _ = self.post(url=f"{self.url}?next={target}")
                self.assertRedirects(response, reverse("workspace"), fetch_redirect_response=False)

    def test_invalid_credentials_show_generic_message_and_keep_email_only(self):
        response, _ = self.post(sign_in={"return_value": (401, {"detail": "Email or password is incorrect."})})
        self.assertContains(response, account_views.INVALID_LOGIN)
        self.assertContains(response, 'value="owner@example.test"')
        self.assertNotContains(response, PASSWORD)
        self.assertNotIn(tokens.REFRESH_TOKEN_KEY, self.client.session)

    def test_throttled_and_unavailable_messages(self):
        response, _ = self.post(sign_in={"return_value": (429, {})})
        self.assertContains(response, account_views.TOO_MANY_SIGN_INS)
        response, _ = self.post(sign_in={"side_effect": ServiceUnavailable("accounts")})
        self.assertContains(response, account_views.SIGN_IN_UNAVAILABLE)

    def test_customers_are_signed_in_and_sent_to_the_food_list(self):
        response, sign_out = self.post(account={"return_value": (200, {**ACCOUNT, "role": "CUSTOMER"})})
        self.assertRedirects(response, reverse("browse-offers"), fetch_redirect_response=False)
        sign_out.assert_not_called()
        self.assertEqual(self.client.session[account_views.ACCOUNT_KEY]["role"], "CUSTOMER")

    def test_customers_never_follow_next_to_a_shop_page(self):
        response, _ = self.post(
            url=f"{self.url}?next={reverse('workspace')}",
            account={"return_value": (200, {**ACCOUNT, "role": "CUSTOMER"})},
        )
        self.assertRedirects(response, reverse("browse-offers"), fetch_redirect_response=False)

    def test_post_without_csrf_token_is_rejected(self):
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.post(self.url, {"email": "owner@example.test", "password": PASSWORD}).status_code, 403)


class ProtectedPageTests(WebAuthTestCase):
    def test_anonymous_visitors_are_sent_to_sign_in_with_next(self):
        for name in ("workspace", "finish-setup"):
            with self.subTest(name=name):
                response = self.client.get(reverse(name))
                self.assertRedirects(response, f"{reverse('sign-in')}?next={reverse(name).replace('/', '%2F')}", fetch_redirect_response=False)

    def test_pages_send_no_store(self):
        self.sign_in_session()
        with patch_service("my_shop", return_value=(200, SHOP)):
            for url in (reverse("workspace"),):
                self.assertIn("no-store", self.client.get(url)["Cache-Control"])
        self.client = Client()
        for url in (reverse("sign-in"), reverse("register")):
            self.assertIn("no-store", self.client.get(url)["Cache-Control"])

    
    def test_expired_access_token_is_renewed_once(self):
        self.sign_in_session(access="old-access")

        with patch_service(
            "my_shop",
            side_effect=[(401, {}), (200, SHOP)],
        ) as my_shop, patch_service(
            "my_listings",
            return_value=(200, []),
        ) as my_listings, patch_service(
            "refresh",
            return_value=(200, {"access": "new-access"}),
        ) as refresh:

            response = self.client.get(reverse("workspace"))

        self.assertContains(response, "Pending review")

        # The expired token should be refreshed only once.
        refresh.assert_called_once_with("refresh-token-1")

        # Both API calls should use the refreshed access token.
        self.assertEqual(
            my_shop.call_args_list[-1].args,
            ("new-access",),
        )
        my_listings.assert_called_once_with("new-access")

        self.assertEqual(
            self.client.session[tokens.ACCESS_TOKEN_KEY],
            "new-access",
        )

    def test_rejected_refresh_token_ends_the_session(self):
        key = self.sign_in_session()
        with patch_service("my_shop", return_value=(401, {})), patch_service("refresh", return_value=(401, {})):
            response = self.client.get(reverse("workspace"))
        self.assertRedirects(response, reverse("sign-in"), fetch_redirect_response=False)
        self.assertFalse(Session.objects.filter(session_key=key).exists())

    def test_operator_without_shop_is_sent_to_finish_setup(self):
        self.sign_in_session()
        with patch_service("my_shop", return_value=(404, {})):
            response = self.client.get(reverse("workspace"))
        self.assertRedirects(response, reverse("finish-setup"), fetch_redirect_response=False)

    def test_unavailable_marketplace_does_not_invent_a_status(self):
        self.sign_in_session()
        with patch_service("my_shop", side_effect=ServiceUnavailable("marketplace")):
            response = self.client.get(reverse("workspace"))
        self.assertContains(response, "status can't be shown")
        self.assertNotContains(response, "Pending review")

    def test_each_status_is_explained(self):
        self.sign_in_session()
        for status, label in (("APPROVED", "Approved"), ("REJECTED", "Not approved")):
            with self.subTest(status=status), patch_service("my_shop", return_value=(200, {**SHOP, "approval_status": status})):
                self.assertContains(self.client.get(reverse("workspace")), label)


class SignOutTests(WebAuthTestCase):
    url = reverse("sign-out")

    def test_get_is_not_allowed(self):
        self.sign_in_session()
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.assertIn(tokens.REFRESH_TOKEN_KEY, self.client.session)

    def test_post_without_csrf_token_keeps_the_session(self):
        client = Client(enforce_csrf_checks=True)
        session = client.session
        session[tokens.REFRESH_TOKEN_KEY] = "refresh-token-1"
        session[account_views.ACCOUNT_KEY] = ACCOUNT
        session.save()
        with patch_service("sign_out") as sign_out:
            self.assertEqual(client.post(self.url).status_code, 403)
        sign_out.assert_not_called()
        self.assertTrue(Session.objects.filter(session_key=session.session_key).exists())

    def test_revokes_refresh_token_and_deletes_the_session(self):
        key = self.sign_in_session()
        with patch_service("sign_out", return_value=(200, {})) as sign_out:
            response = self.client.post(self.url, follow=True)
        sign_out.assert_called_once_with("refresh-token-1")
        self.assertFalse(Session.objects.filter(session_key=key).exists())
        self.assertContains(response, "You have signed out.")
        self.assertRedirects(self.client.get(reverse("workspace")), f"{reverse('sign-in')}?next=%2Fworkspace%2F", fetch_redirect_response=False)

    def test_service_failure_still_clears_session_and_says_so(self):
        key = self.sign_in_session()
        with patch_service("sign_out", side_effect=ServiceUnavailable("accounts")):
            response = self.client.post(self.url, follow=True)
        self.assertFalse(Session.objects.filter(session_key=key).exists())
        self.assertContains(response, "couldn&#x27;t reach the account service to revoke")
        self.assertNotIn(tokens.REFRESH_TOKEN_KEY, self.client.session)


class ServiceClientTests(TestCase):
    """The client turns transport failures into ServiceUnavailable and keeps secrets out of errors."""

    def test_timeouts_and_server_errors_are_unavailable_but_4xx_are_returned(self):
        from urllib.error import HTTPError, URLError
        import io

        with mock.patch("frontend.services.urlopen", side_effect=TimeoutError):
            with self.assertRaises(ServiceUnavailable) as caught:
                services.sign_in("owner@example.test", PASSWORD)
        self.assertNotIn(PASSWORD, str(caught.exception))
        with mock.patch("frontend.services.urlopen", side_effect=URLError("refused")):
            with self.assertRaises(ServiceUnavailable):
                services.my_shop("access-token-1")
        with mock.patch("frontend.services.urlopen", side_effect=HTTPError("u", 503, "x", {}, io.BytesIO(b"{}"))):
            with self.assertRaises(ServiceUnavailable):
                services.my_shop("access-token-1")
        with mock.patch("frontend.services.urlopen", side_effect=HTTPError("u", 401, "x", {}, io.BytesIO(b'{"detail": "no"}'))):
            self.assertEqual(services.sign_in("owner@example.test", PASSWORD), (401, {"detail": "no"}))


class SignUpChoiceTests(WebAuthTestCase):
    def test_sign_up_asks_customer_or_restaurant_owner(self):
        response = self.client.get(reverse("sign-up"))
        self.assertContains(response, "I'm a customer")
        self.assertContains(response, "I'm a restaurant owner")
        self.assertContains(response, f'href="{reverse("customer-sign-up")}"')
        self.assertContains(response, f'href="{reverse("register")}"')


class CustomerSignUpTests(WebAuthTestCase):
    url = reverse("customer-sign-up")
    CUSTOMER = {"id": 9, "email": "maya@example.test", "display_name": "Maya", "role": "CUSTOMER"}

    def data(self):
        return {"display_name": "Maya", "email": "maya@example.test",
                "password": PASSWORD, "password_confirm": PASSWORD}

    def test_creates_customer_and_shows_the_food(self):
        with patch_service("register_customer", return_value=(201, {**TOKENS, "user": self.CUSTOMER})) as create,                 patch_service("register") as operator_sign_up,                 patch_service("create_shop") as create_shop:
            response = self.client.post(self.url, self.data())
        self.assertRedirects(response, reverse("browse-offers"), fetch_redirect_response=False)
        create.assert_called_once_with("Maya", "maya@example.test", PASSWORD)
        operator_sign_up.assert_not_called()
        create_shop.assert_not_called()
        self.assertEqual(self.client.session[account_views.ACCOUNT_KEY]["role"], "CUSTOMER")

    def test_duplicate_email_is_shown_on_the_form(self):
        with patch_service("register_customer",
                           return_value=(400, {"email": ["An account with this email already exists."]})):
            response = self.client.post(self.url, self.data())
        self.assertContains(response, "An account with this email already exists.")
        self.assertNotIn(tokens.REFRESH_TOKEN_KEY, self.client.session)

    def test_signed_in_customer_cannot_open_shop_pages(self):
        session = self.client.session
        session[tokens.ACCESS_TOKEN_KEY] = "a"
        session[tokens.REFRESH_TOKEN_KEY] = "r"
        session[account_views.ACCOUNT_KEY] = self.CUSTOMER
        session.save()
        with patch_service("my_shop") as my_shop:
            for name in ("workspace", "shop-profile", "create-listing"):
                with self.subTest(name):
                    response = self.client.get(reverse(name))
                    self.assertRedirects(response, reverse("browse-offers"), fetch_redirect_response=False)
        my_shop.assert_not_called()
