"""LP-56: one login page for everyone, sign-up for customers and restaurants,
role-based landing pages and role-based page access."""

from django.test import override_settings
from django.urls import reverse

from . import account_views, tokens
from .test_accounts import ACCOUNT, PASSWORD, TOKENS, WebAuthTestCase, patch_service
from .test_lp14_permissions import NoServiceCalls, protected_pages

CUSTOMER = {"id": 8, "email": "rami@example.test", "display_name": "Rami", "role": "CUSTOMER", "is_staff": False}
OPERATOR = {**ACCOUNT, "is_staff": False}
STAFF = {"id": 9, "email": "team@example.test", "display_name": "Team", "role": "CUSTOMER", "is_staff": True}
ADMIN_URL = "http://admin.example.test/admin/"


def signup_data(**overrides):
    data = {"account_type": "customer", "display_name": "Rami", "email": "rami@example.test", "password": PASSWORD}
    data.update(overrides)
    return data


@override_settings(STAFF_ADMIN_URL=ADMIN_URL)
class LoginTests(WebAuthTestCase):
    url = reverse("login")

    def log_in(self, account, data=None, sign_in=(200, TOKENS)):
        with patch_service("sign_in", return_value=sign_in) as sign_in_api, \
                patch_service("account", return_value=(200, account)), \
                patch_service("sign_out", return_value=(200, {})) as sign_out:
            response = self.client.post(self.url, data or {"email": account["email"], "password": PASSWORD})
        return response, sign_in_api, sign_out

    def test_page_has_the_login_fields_and_a_sign_up_link(self):
        response = self.client.get(self.url)
        for text in ('name="email"', 'name="password"', 'name="remember_me"', "Keep me logged in",
                     ">Log in</button>", "New here?", f'href="{reverse("signup")}"'):
            self.assertContains(response, text)

    def test_each_role_lands_on_its_own_page(self):
        for account, landing in ((CUSTOMER, reverse("home")), (OPERATOR, reverse("workspace")), (STAFF, ADMIN_URL)):
            with self.subTest(account["display_name"]):
                self.client = self.client_class()
                response, _, _ = self.log_in(account)
                self.assertRedirects(response, landing, fetch_redirect_response=False)

    def test_staff_get_no_web_session_and_their_token_is_revoked(self):
        response, _, sign_out = self.log_in(STAFF)

        self.assertRedirects(response, ADMIN_URL, fetch_redirect_response=False)
        sign_out.assert_called_once_with("refresh-token-1")
        self.assertNotIn(tokens.REFRESH_TOKEN_KEY, self.client.session)
        self.assertNotIn(account_views.ACCOUNT_KEY, self.client.session)

    def test_bad_login_shows_one_generic_error(self):
        response, _, _ = self.log_in(CUSTOMER, sign_in=(401, {"detail": "No active account found"}))

        self.assertContains(response, account_views.INVALID_LOGIN)
        self.assertNotContains(response, "No active account")
        self.assertNotContains(response, PASSWORD)
        self.assertNotIn(tokens.REFRESH_TOKEN_KEY, self.client.session)

    def test_password_is_sent_exactly_as_typed(self):
        spaced = f"  {PASSWORD}  "
        _, sign_in_api, _ = self.log_in(CUSTOMER, {"email": "rami@example.test", "password": spaced})
        sign_in_api.assert_called_once_with("rami@example.test", spaced)

    def test_keep_me_logged_in_decides_how_long_the_session_lasts(self):
        self.log_in(CUSTOMER, {"email": "rami@example.test", "password": PASSWORD, "remember_me": "on"})
        self.assertFalse(self.client.session.get_expire_at_browser_close())

        self.client = self.client_class()
        self.log_in(CUSTOMER, {"email": "rami@example.test", "password": PASSWORD})
        self.assertTrue(self.client.session.get_expire_at_browser_close())

    def test_signed_in_visitors_are_sent_to_their_landing_page(self):
        self.log_in(CUSTOMER)
        self.assertRedirects(self.client.get(self.url), reverse("home"), fetch_redirect_response=False)
        self.assertRedirects(self.client.get(reverse("signup")), reverse("home"), fetch_redirect_response=False)


class SignUpTests(WebAuthTestCase):
    url = reverse("signup")

    def test_page_offers_only_the_two_account_types(self):
        response = self.client.get(self.url)

        self.assertContains(response, "How will you use LastPlate?")
        self.assertContains(response, "I want to buy food")
        self.assertContains(response, "I own a restaurant")
        self.assertContains(response, 'name="account_type"', count=2)
        for word in ("admin", "Admin", "staff", "Staff"):
            self.assertNotContains(response, f'value="{word}')

    def test_customer_is_logged_in_and_lands_on_home(self):
        with patch_service("register", return_value=(201, {**TOKENS, "user": CUSTOMER})) as register, \
                patch_service("create_shop") as create_shop:
            response = self.client.post(self.url, signup_data())

        self.assertRedirects(response, reverse("home"), fetch_redirect_response=False)
        register.assert_called_once_with("Rami", "rami@example.test", PASSWORD, "customer")
        create_shop.assert_not_called()
        self.assertEqual(self.client.session[tokens.REFRESH_TOKEN_KEY], "refresh-token-1")
        self.assertEqual(self.client.session[account_views.ACCOUNT_KEY]["role"], "CUSTOMER")

    def test_restaurant_lands_on_finish_setup(self):
        with patch_service("register", return_value=(201, {**TOKENS, "user": OPERATOR})) as register:
            response = self.client.post(self.url, signup_data(account_type="restaurant", email=OPERATOR["email"]))

        self.assertRedirects(response, reverse("finish-setup"), fetch_redirect_response=False)
        self.assertEqual(register.call_args.args[3], "restaurant")
        self.assertEqual(self.client.session[account_views.ACCOUNT_KEY]["role"], "SHOP_OPERATOR")

    def test_forged_account_types_are_rejected_without_calling_accounts(self):
        for forged in ("admin", "staff", "ADMIN", "SHOP_OPERATOR", "CUSTOMER", ""):
            with self.subTest(forged=forged), NoServiceCalls() as services_:
                response = self.client.post(self.url, signup_data(account_type=forged))
                self.assertEqual(response.status_code, 200)
                services_.assert_none_called(self)
                self.assertNotIn(tokens.REFRESH_TOKEN_KEY, self.client.session)

    def test_forged_role_and_privilege_fields_are_never_forwarded(self):
        forged = signup_data(role="ADMIN", is_staff="true", is_superuser="true")
        with patch_service("register", return_value=(201, {**TOKENS, "user": CUSTOMER})) as register:
            self.client.post(self.url, forged)

        register.assert_called_once_with("Rami", "rami@example.test", PASSWORD, "customer")

    def test_password_is_not_trimmed(self):
        spaced = f"  {PASSWORD}  "
        with patch_service("register", return_value=(201, {**TOKENS, "user": CUSTOMER})) as register:
            self.client.post(self.url, signup_data(password=spaced))

        self.assertEqual(register.call_args.args[2], spaced)


@override_settings(STAFF_ADMIN_URL=ADMIN_URL)
class CrossRoleAccessTests(WebAuthTestCase):

    def sign_in_as(self, account):
        self.sign_in_session()
        session = self.client.session
        session[account_views.ACCOUNT_KEY] = {key: account[key] for key in ("id", "email", "display_name", "role")}
        session.save()

    def test_customers_cannot_open_shop_pages(self):
        self.sign_in_as(CUSTOMER)
        for method, url in protected_pages():
            with self.subTest(f"{method.upper()} {url}"), NoServiceCalls() as services_:
                response = getattr(self.client, method)(url)
                self.assertRedirects(response, reverse("home"), fetch_redirect_response=False)
                services_.assert_none_called(self)

        page = self.client.get(reverse("home"))
        self.assertContains(page, account_views.SHOPS_ONLY)

    def test_shops_cannot_open_customer_pages(self):
        self.sign_in_as(OPERATOR)
        with NoServiceCalls() as services_:
            response = self.client.get(reverse("my-account"))
        self.assertRedirects(response, reverse("workspace"), fetch_redirect_response=False)
        services_.assert_none_called(self)

    def test_customers_can_open_their_account_page(self):
        self.sign_in_as(CUSTOMER)
        response = self.client.get(reverse("my-account"))

        self.assertContains(response, "Rami")
        self.assertContains(response, "rami@example.test")
        self.assertIn("no-store", response["Cache-Control"])

    def test_anonymous_visitors_are_asked_to_log_in(self):
        response = self.client.get(reverse("my-account"))
        self.assertRedirects(response, f"{reverse('login')}?next=%2Faccount%2F", fetch_redirect_response=False)

    def test_navigation_matches_the_role(self):
        self.sign_in_as(CUSTOMER)
        with patch_service("browse_listings", return_value=(200, [])):
            page = self.client.get(reverse("browse-offers"))
        self.assertContains(page, f'href="{reverse("my-account")}"')
        self.assertNotContains(page, f'href="{reverse("workspace")}"')


class OldAddressTests(WebAuthTestCase):

    def test_old_sign_in_and_register_addresses_redirect(self):
        for old, new in (("/sign-in/", reverse("login")), ("/register/", reverse("signup"))):
            with self.subTest(old):
                self.assertRedirects(self.client.get(old), new, fetch_redirect_response=False)

    def test_next_is_kept_when_redirecting(self):
        response = self.client.get("/sign-in/?next=/workspace/")
        self.assertRedirects(response, f"{reverse('login')}?next=/workspace/", fetch_redirect_response=False)
