"""LP-14: Permission audit tests for every web frontend page."""

from contextlib import ExitStack
from unittest import mock
from urllib.parse import urlencode

from django.test import Client
from django.urls import reverse

from . import account_views, services, tokens
from .test_accounts import ACCOUNT, ApprovedShopMixin, WebAuthTestCase, patch_service

# Every function the web frontend uses to call a backend service.
SERVICE_CALLS = (
    "register", "sign_in", "account", "refresh", "sign_out", "create_shop", "my_shop",
    "update_shop", "my_listings", "create_listing", "upload_listing_photo", "edit_listing",
    "publish_listing", "adjust_stock", "stock_history",
    "admin_summary", "admin_shops", "admin_shop", "admin_review_shop",
)

DRAFT = {"id": 42, "title": "Croissant Box", "status": "DRAFT", "photo_display": None}


def protected_pages():
    """(method, url) for every page that needs a signed-in shop operator."""
    return [
        ("get", reverse("workspace")),
        ("get", reverse("finish-setup")),
        ("post", reverse("finish-setup")),
        ("get", reverse("shop-profile")),
        ("post", reverse("shop-profile")),
        ("get", reverse("create-listing")),
        ("post", reverse("create-listing")),
        ("get", reverse("edit-listing", args=[42])),
        ("post", reverse("edit-listing", args=[42])),
        ("post", reverse("publish-listing", args=[42])),
        ("post", reverse("adjust-stock", args=[42])),
    ]


class NoServiceCalls:
    """Patch every service call so a test can prove none was made."""

    def __enter__(self):
        self.stack = ExitStack()
        self.mocks = {name: self.stack.enter_context(patch_service(name)) for name in SERVICE_CALLS}
        return self

    def __exit__(self, *exc):
        self.stack.close()

    def assert_none_called(self, test):
        for name, call in self.mocks.items():
            test.assertFalse(call.called, f"services.{name} was called")


class AnonymousIsBlockedTests(WebAuthTestCase):

    def assert_sent_to_sign_in(self, method, url):
        with NoServiceCalls() as services_:
            if method == "post":
                response = self.client.post(url, {"title": "x", "quantity_change": "1", "reason": "r"})
            else:
                response = self.client.get(url)
        self.assertEqual(response.status_code, 302, f"{method.upper()} {url}")
        self.assertEqual(response["Location"], f"{reverse('login')}?{urlencode({'next': url})}")
        services_.assert_none_called(self)

    def test_every_protected_page_redirects_to_sign_in_without_calling_services(self):
        for method, url in protected_pages():
            with self.subTest(f"{method.upper()} {url}"):
                self.assert_sent_to_sign_in(method, url)

    def test_half_a_session_is_not_a_sign_in(self):
        # An account record without a refresh token (or the reverse) is anonymous.
        for key, value in ((account_views.ACCOUNT_KEY, ACCOUNT), (tokens.REFRESH_TOKEN_KEY, "refresh-token-1")):
            with self.subTest(key):
                self.client = self.client_class()
                session = self.client.session
                session[key] = value
                session.save()
                self.assert_sent_to_sign_in("get", reverse("workspace"))

    def test_public_pages_stay_public(self):
        with patch_service("browse_listings", return_value=(200, [])), \
                patch_service("listing_offer", return_value=(404, {})):
            for url in (reverse("browse-offers"), reverse("signup"), reverse("login")):
                with self.subTest(url):
                    self.assertEqual(self.client.get(url).status_code, 200)
            self.assertEqual(self.client.get(reverse("offer-detail", args=[42])).status_code, 404)


class WrongRoleIsBlockedTests(WebAuthTestCase):

    def test_customer_session_opens_no_shop_page_and_calls_no_service(self):
        customer = {**ACCOUNT, "role": "CUSTOMER"}
        with patch_service("sign_in", return_value=(200, {"access": "a", "refresh": "r"})), \
                patch_service("account", return_value=(200, customer)):
            self.client.post(reverse("login"), {"email": "c@example.test", "password": "pw-Local-1"})
        self.assertIn(tokens.REFRESH_TOKEN_KEY, self.client.session)

        for method, url in protected_pages():
            with self.subTest(f"{method.upper()} {url}"), NoServiceCalls() as services_:
                response = getattr(self.client, method)(url)
                self.assertRedirects(response, reverse("home"), fetch_redirect_response=False)
                services_.assert_none_called(self)


class SignedInOperatorTests(ApprovedShopMixin, WebAuthTestCase):

    def setUp(self):
        self.sign_in_session()

    def test_post_only_pages_refuse_get(self):
        for url in (reverse("publish-listing", args=[42]), reverse("adjust-stock", args=[42]), reverse("sign-out")):
            with self.subTest(url), NoServiceCalls() as services_:
                self.assertEqual(self.client.get(url).status_code, 405)
                services_.assert_none_called(self)

    def test_every_form_post_needs_a_csrf_token(self):
        client = Client(enforce_csrf_checks=True)
        client.cookies = self.client.cookies
        for method, url in protected_pages() + [("post", reverse("sign-out"))]:
            if method != "post":
                continue
            with self.subTest(url), NoServiceCalls() as services_:
                self.assertEqual(client.post(url, {"title": "x"}).status_code, 403)
                services_.assert_none_called(self)
        self.assertIn(tokens.REFRESH_TOKEN_KEY, self.client.session)

    def test_another_operators_listing_cannot_be_edited_from_the_web(self):
        # Marketplace returns only this operator's listings; 99 is not among them.
        with patch_service("my_listings", return_value=(200, [DRAFT])), \
                patch_service("edit_listing") as edit_api:
            response = self.client.post(reverse("edit-listing", args=[99]), {"title": "Hijack"})

        self.assertEqual(response.status_code, 404)
        edit_api.assert_not_called()

    def test_protected_fields_in_the_form_are_never_forwarded(self):
        forged = {
            "title": "Box", "status": "PUBLISHED", "shop_id": "1", "terms_revision": "9",
            "first_reserved_at": "2026-01-01T10:00", "operator_user_id": "1", "id": "5",
        }
        with patch_service("create_listing", return_value=(201, {"id": 5})) as create_api, \
                patch_service("my_listings", return_value=(200, [DRAFT])), \
                patch_service("edit_listing", return_value=(200, DRAFT)) as edit_api, \
                patch_service("update_shop", return_value=(200, {})) as update_api, \
                patch_service("my_shop", return_value=(200, {"approval_status": "APPROVED"})):
            self.client.post(reverse("create-listing"), forged)
            self.client.post(reverse("edit-listing", args=[42]), forged)
            self.client.post(reverse("shop-profile"), {
                "name": "Cedar", "category": "Bakery", "address": "Main street 1", "area": "Hamra",
                "public_contact": "+961 1 234 567", "approval_status": "APPROVED",
                "operator_user_id": "1", "reviewed_by": "1",
            })

        sent = [
            create_api.call_args.args[1],
            edit_api.call_args.args[2],
            update_api.call_args.args[1],
        ]
        for payload in sent:
            for field in ("status", "shop_id", "terms_revision", "first_reserved_at",
                          "operator_user_id", "id", "approval_status", "reviewed_by"):
                self.assertNotIn(field, payload)

    def test_stock_form_sends_only_the_change_and_reason(self):
        with patch_service("adjust_stock", return_value=(201, {"listing": {}})) as adjust_api, \
                mock.patch.object(services, "my_shop", return_value=(200, {"approval_status": "APPROVED"})):
            self.client.post(
                reverse("adjust-stock", args=[42]),
                {"quantity_change": "2", "reason": "Baked more", "actor_user_id": "1", "kind": "SHOP_CANCELLATION"},
            )

        adjust_api.assert_called_once_with("access-token-1", 42, 2, "Baked more")
