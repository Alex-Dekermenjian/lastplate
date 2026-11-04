"""LP-14: Permission audit tests for every Marketplace API endpoint.

These use real RS256 tokens (not force_authenticate), so each request goes
through AccessTokenAuthentication exactly as in production.
"""

import base64
import json
from datetime import timedelta
from io import BytesIO

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import URLPattern, URLResolver, get_resolver, reverse
from django.utils import timezone
from PIL import Image
from rest_framework.authentication import SessionAuthentication
from rest_framework.test import APIClient, APITestCase

from . import views
from .models import Listing, Shop, StockAdjustment
from .test_shops import OTHER_PRIVATE_KEY, TEST_PUBLIC_KEY, make_token

User = get_user_model()

OPERATOR_A = 101
OPERATOR_B = 202


def png_upload(name="photo.png"):
    buffer = BytesIO()
    Image.new("RGB", (8, 8), "green").save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


def approved_shop(operator_user_id, name):
    return Shop.objects.create(
        operator_user_id=operator_user_id,
        name=name,
        address="Main street 1",
        area="Hamra",
        public_contact="+961 1 234 567",
        approval_status=Shop.ApprovalStatus.APPROVED,
    )


def published_listing(shop, **overrides):
    start = timezone.now() + timedelta(hours=2)
    values = {
        "shop_id": shop.id,
        "title": "Croissant Box",
        "description": "Fresh pastries",
        "category": "Bakery",
        "original_price": "10.00",
        "discounted_price": "5.00",
        "total_quantity": 5,
        "pickup_address": "Main street 1",
        "pickup_start": start,
        "pickup_end": start + timedelta(hours=2),
        "status": Listing.Status.PUBLISHED,
    }
    values.update(overrides)
    return Listing.objects.create(**values)


@override_settings(JWT_PUBLIC_KEY=TEST_PUBLIC_KEY)
class PermissionTestCase(APITestCase):

    def setUp(self):
        self.shop_a = approved_shop(OPERATOR_A, "Shop A")
        self.shop_b = approved_shop(OPERATOR_B, "Shop B")
        self.draft_b = Listing.objects.create(
            shop_id=self.shop_b.id,
            title="B draft",
            original_price="10.00",
            discounted_price="5.00",
            total_quantity=3,
        )
        self.published_b = published_listing(self.shop_b, title="B offer")
        StockAdjustment.objects.create(
            listing=self.published_b,
            quantity_change=2,
            reason="Baked more",
            actor_user_id=OPERATOR_B,
        )

    def as_token(self, token):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    def protected_requests(self):
        """Every Marketplace API request that needs a shop operator."""
        draft, published = self.draft_b.pk, self.published_b.pk
        return [
            ("post", reverse("shop-create"), {"name": "New shop"}, "json"),
            ("get", reverse("shop-me"), None, None),
            ("patch", reverse("shop-me"), {"area": "Badaro"}, "json"),
            ("post", reverse("listings"), {"title": "X"}, "json"),
            ("get", reverse("my_listings"), None, None),
            ("patch", reverse("listing_detail", args=[draft]), {"title": "X"}, "json"),
            ("post", reverse("publish_listing", args=[draft]), None, None),
            ("post", reverse("upload_listing_photo", args=[draft]), "photo", "multipart"),
            ("get", reverse("listing_stock_adjustments", args=[published]), None, None),
            ("post", reverse("listing_stock_adjustments", args=[published]),
             {"quantity_change": 1, "reason": "More"}, "json"),
        ]

    def send(self, method, url, data, fmt):
        if data == "photo":
            data = {"photo": png_upload()}
        kwargs = {"format": fmt} if fmt else {}
        return getattr(self.client, method)(url, data, **kwargs)

    def snapshot(self):
        """Everything a blocked request must leave untouched."""
        return (
            list(Shop.objects.order_by("pk").values()),
            list(Listing.objects.order_by("pk").values()),
            list(StockAdjustment.objects.order_by("pk").values()),
        )


class AnonymousIsBlockedTests(PermissionTestCase):

    def test_every_protected_endpoint_returns_401_without_a_token(self):
        before = self.snapshot()
        for method, url, data, fmt in self.protected_requests():
            with self.subTest(f"{method.upper()} {url}"):
                response = self.send(method, url, data, fmt)
                self.assertEqual(response.status_code, 401)
        self.assertEqual(self.snapshot(), before)


class WrongRoleIsBlockedTests(PermissionTestCase):

    def test_customer_and_unknown_roles_get_403_on_every_operator_endpoint(self):
        before = self.snapshot()
        # A customer who happens to share an operator's user ID still gets nothing.
        for role in ("CUSTOMER", "", "ADMIN", "shop_operator"):
            self.as_token(make_token(user_id=OPERATOR_B, role=role))
            for method, url, data, fmt in self.protected_requests():
                with self.subTest(f"{role!r} {method.upper()} {url}"):
                    response = self.send(method, url, data, fmt)
                    self.assertEqual(response.status_code, 403)
        self.assertEqual(self.snapshot(), before)

    def test_staff_claim_does_not_unlock_operator_endpoints(self):
        self.as_token(make_token(user_id=OPERATOR_B, role="CUSTOMER", is_staff=True))

        response = self.client.get(reverse("my_listings"))

        self.assertEqual(response.status_code, 403)

    def test_operator_token_is_not_an_admin_sign_in(self):
        # Shop review is staff-only and lives in the session-based admin.
        self.as_token(make_token(user_id=OPERATOR_A, is_staff=True))

        for url in (
            reverse("admin:marketplace_shop_changelist"),
            reverse("admin:marketplace_shop_change", args=[self.shop_b.pk]),
        ):
            with self.subTest(url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse("admin:login"), response["Location"])

        response = self.client.post(
            reverse("admin:marketplace_shop_change", args=[self.shop_b.pk]),
            {"approval_status": "REJECTED", "rejection_reason": "No"},
        )
        self.assertIn(response.status_code, (302, 403))
        self.shop_b.refresh_from_db()
        self.assertEqual(self.shop_b.approval_status, "APPROVED")


class CrossOperatorTests(PermissionTestCase):
    """Operator A must never read or change operator B's data."""

    def setUp(self):
        super().setUp()
        self.as_token(make_token(user_id=OPERATOR_A))

    def test_a_cannot_read_or_change_b(self):
        before = self.snapshot()
        draft, published = self.draft_b.pk, self.published_b.pk

        cases = [
            ("edit B's draft", "patch", reverse("listing_detail", args=[draft]), {"title": "Hijacked"}, "json"),
            ("publish B's draft", "post", reverse("publish_listing", args=[draft]), None, None),
            ("upload B's photo", "post", reverse("upload_listing_photo", args=[draft]), "photo", "multipart"),
            ("read B's stock history", "get", reverse("listing_stock_adjustments", args=[published]), None, None),
            ("adjust B's stock", "post", reverse("listing_stock_adjustments", args=[published]),
             {"quantity_change": -5, "reason": "Sabotage"}, "json"),
        ]
        for label, method, url, data, fmt in cases:
            with self.subTest(label):
                response = self.send(method, url, data, fmt)
                self.assertIn(response.status_code, (403, 404))
                body = json.dumps(getattr(response, "data", None), default=str)
                self.assertNotIn("B draft", body)
                self.assertNotIn("Baked more", body)

        self.assertEqual(self.snapshot(), before)

    def test_a_only_ever_sees_own_shop_and_listings(self):
        mine = Listing.objects.create(shop_id=self.shop_a.id, title="A draft")

        shop = self.client.get(reverse("shop-me")).data
        listings = self.client.get(reverse("my_listings")).data

        self.assertEqual(shop["id"], self.shop_a.id)
        self.assertEqual([item["id"] for item in listings], [mine.id])

    def test_shop_endpoints_take_no_shop_id_from_the_request(self):
        response = self.client.patch(
            reverse("shop-me"),
            {"id": self.shop_b.id, "operator_user_id": OPERATOR_A},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.shop_b.refresh_from_db()
        self.assertEqual(self.shop_b.operator_user_id, OPERATOR_B)

    def test_create_cannot_target_b_shop(self):
        response = self.client.post(
            reverse("listings"),
            {"title": "Planted", "shop_id": self.shop_b.id},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Listing.objects.filter(title="Planted").exists())


class ProtectedFieldTests(PermissionTestCase):
    """Server-owned fields can never be set from a request."""

    def setUp(self):
        super().setUp()
        self.as_token(make_token(user_id=OPERATOR_A))
        self.draft_a = Listing.objects.create(shop_id=self.shop_a.id, title="A draft")

    def test_listing_create_rejects_protected_fields(self):
        protected = {
            "status": "PUBLISHED",
            "shop_id": self.shop_b.id,
            "terms_revision": 9,
            "first_reserved_at": "2026-01-01T10:00:00+02:00",
            "id": 999,
        }
        for field, value in protected.items():
            with self.subTest(field):
                response = self.client.post(
                    reverse("listings"),
                    {"title": "Sneaky", field: value},
                    format="json",
                )
                self.assertEqual(response.status_code, 400)
        self.assertFalse(Listing.objects.filter(title="Sneaky").exists())

    def test_listing_edit_rejects_protected_fields(self):
        protected = {
            "status": "PUBLISHED",
            "shop_id": self.shop_b.id,
            "terms_revision": 9,
            "first_reserved_at": "2026-01-01T10:00:00+02:00",
        }
        for field, value in protected.items():
            with self.subTest(field):
                response = self.client.patch(
                    reverse("listing_detail", args=[self.draft_a.pk]),
                    {field: value},
                    format="json",
                )
                self.assertEqual(response.status_code, 400)

        self.draft_a.refresh_from_db()
        self.assertEqual(self.draft_a.status, Listing.Status.DRAFT)
        self.assertEqual(self.draft_a.shop_id, self.shop_a.id)
        self.assertEqual(self.draft_a.terms_revision, 1)
        self.assertIsNone(self.draft_a.first_reserved_at)

    def test_shop_create_and_profile_reject_review_and_ownership_fields(self):
        Shop.objects.filter(pk=self.shop_a.pk).update(approval_status="PENDING")
        reviewer = User.objects.create_user(username="reviewer", password="x-Local-test-only-1")
        protected = {
            "approval_status": "APPROVED",
            "operator_user_id": OPERATOR_B,
            "reviewed_by": reviewer.pk,
            "reviewed_at": "2026-01-01T10:00:00+02:00",
            "rejection_reason": "",
        }
        for field, value in protected.items():
            with self.subTest(f"create {field}"):
                response = self.client.post(
                    reverse("shop-create"), {"name": "Shop A", field: value}, format="json"
                )
                self.assertEqual(response.status_code, 400)
            with self.subTest(f"profile {field}"):
                response = self.client.patch(reverse("shop-me"), {field: value}, format="json")
                self.assertEqual(response.status_code, 400)

        self.shop_a.refresh_from_db()
        self.assertEqual(self.shop_a.approval_status, "PENDING")
        self.assertEqual(self.shop_a.operator_user_id, OPERATOR_A)
        self.assertIsNone(self.shop_a.reviewed_by)

    def test_publish_and_stock_reject_server_owned_fields(self):
        mine = published_listing(self.shop_a)
        publish = self.client.post(
            reverse("publish_listing", args=[self.draft_a.pk]),
            {"approval_status": "APPROVED"},
            format="json",
        )
        stock = self.client.post(
            reverse("listing_stock_adjustments", args=[mine.pk]),
            {"quantity_change": 1, "reason": "x", "actor_user_id": OPERATOR_B, "kind": "SHOP_CANCELLATION"},
            format="json",
        )

        self.assertEqual(publish.status_code, 400)
        self.assertEqual(stock.status_code, 400)
        self.assertFalse(StockAdjustment.objects.filter(listing=mine).exists())


class TokenRejectionTests(PermissionTestCase):

    def assert_rejected_everywhere(self, token):
        self.as_token(token)
        before = self.snapshot()
        for method, url, data, fmt in self.protected_requests():
            response = self.send(method, url, data, fmt)
            self.assertEqual(response.status_code, 401, f"{method.upper()} {url}")
        self.assertEqual(self.snapshot(), before)

    def tampered(self):
        """A valid token whose payload was edited to claim another user."""
        header, payload, signature = make_token(user_id=OPERATOR_A).split(".")
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        claims["user_id"] = str(OPERATOR_B)
        forged = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
        return f"{header}.{forged}.{signature}"

    def test_invalid_expired_tampered_and_wrong_signature_tokens(self):
        cases = {
            "garbage": "not-a-token",
            "empty segments": "..",
            "expired": make_token(user_id=OPERATOR_B, expires_in=-1),
            "tampered payload": self.tampered(),
            "wrong signature": make_token(user_id=OPERATOR_B, key=OTHER_PRIVATE_KEY),
            "refresh token": make_token(user_id=OPERATOR_B, token_type="refresh"),
            "zero user id": make_token(user_id=0),
        }
        for label, token in cases.items():
            with self.subTest(label):
                self.assert_rejected_everywhere(token)

    def test_public_pages_ignore_bad_tokens_and_still_hide_drafts(self):
        self.as_token(self.tampered())

        browse = self.client.get(reverse("listings"))
        draft = self.client.get(reverse("listing_detail", args=[self.draft_b.pk]))

        self.assertEqual(browse.status_code, 200)
        self.assertEqual([item["id"] for item in browse.data], [self.published_b.id])
        self.assertEqual(draft.status_code, 404)


class CsrfExemptDispatcherTests(PermissionTestCase):
    """listings_collection and listing_resource are CSRF-exempt. That is only
    safe because Marketplace has no cookie-based authentication at all."""

    def setUp(self):
        super().setUp()
        # A real browser session: a signed-in Marketplace superuser.
        admin = User.objects.create_superuser(username="root", password="x-Local-test-only-1")
        self.browser = APIClient(enforce_csrf_checks=True)
        self.browser.force_login(admin)

    def test_cookie_only_requests_cannot_write_through_the_dispatcher(self):
        before = self.snapshot()

        create = self.browser.post(reverse("listings"), {"title": "CSRF"}, format="json")
        edit = self.browser.patch(
            reverse("listing_detail", args=[self.draft_b.pk]), {"title": "CSRF"}, format="json"
        )
        form_post = self.browser.post(reverse("listings"), {"title": "CSRF"})

        for response in (create, edit, form_post):
            self.assertEqual(response.status_code, 401)
        self.assertEqual(self.snapshot(), before)

    def test_other_methods_on_the_dispatcher_are_refused(self):
        self.as_token(make_token(user_id=OPERATOR_B))
        for method, url in (
            ("put", reverse("listings")),
            ("delete", reverse("listings")),
            ("put", reverse("listing_detail", args=[self.draft_b.pk])),
            ("delete", reverse("listing_detail", args=[self.draft_b.pk])),
        ):
            with self.subTest(f"{method.upper()} {url}"):
                self.assertEqual(getattr(self.client, method)(url).status_code, 405)
        self.assertTrue(Listing.objects.filter(pk=self.draft_b.pk).exists())

    def test_no_api_view_accepts_session_authentication(self):
        self.assertNotIn(
            "rest_framework.authentication.SessionAuthentication",
            settings.REST_FRAMEWORK["DEFAULT_AUTHENTICATION_CLASSES"],
        )

        def callbacks(patterns):
            for entry in patterns:
                if isinstance(entry, URLResolver):
                    if entry.app_name != "admin":
                        yield from callbacks(entry.url_patterns)
                elif isinstance(entry, URLPattern):
                    yield entry.callback

        # The dispatchers are plain functions; check the DRF views they call.
        targets = {
            views.listings_collection: [views.browse_listings, views.create_listing],
            views.listing_resource: [views.listing_offer, views.edit_listing],
        }
        checked = 0
        for callback in callbacks(get_resolver().url_patterns):
            for view in targets.get(callback, [callback]):
                cls = getattr(view, "cls", None) or getattr(view, "view_class", None)
                if cls is None or not hasattr(cls, "get_authenticators"):
                    continue
                for auth in cls().get_authenticators():
                    self.assertNotIsInstance(auth, SessionAuthentication, view)
                checked += 1
        self.assertGreaterEqual(checked, 10)
