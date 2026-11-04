"""Regression tests for the Sprint 1 local repairs."""

import socket
from datetime import timedelta
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import services
from .listing_photos import photo_url
from .services import ServiceUnavailable
from .test_accounts import WebAuthTestCase, patch_service
from .test_shop_profile import COMPLETE_SHOP


class NoLiveServiceTests(SimpleTestCase):
    def test_a_running_service_is_never_contacted(self):
        """The real regression: inside Docker, unmocked calls reached Marketplace."""
        listener = socket.socket()
        self.addCleanup(listener.close)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(0.2)
        url = f"http://127.0.0.1:{listener.getsockname()[1]}"

        with override_settings(SERVICE_URLS={"marketplace": url}):
            with self.assertRaises(ServiceUnavailable):
                services.my_listings("token")

        with self.assertRaises(TimeoutError):
            listener.accept()


class WorkspaceWithoutMocksTests(WebAuthTestCase):
    def test_workspace_does_not_end_the_session_when_listings_are_not_mocked(self):
        self.sign_in_session()
        with patch_service("my_shop", return_value=(200, COMPLETE_SHOP)):
            response = self.client.get(reverse("workspace"))
        self.assertContains(response, "couldn't load your food offers")


class PhotoUrlTests(SimpleTestCase):
    PATH = "/media/listing_photos/42/photo.jpg"

    @override_settings(
        DEBUG=True,
        SERVICE_URLS={"marketplace": "http://marketplace:8000"},
        MARKETPLACE_PUBLIC_URL="http://127.0.0.1:8002",
    )
    def test_uses_the_browser_address_not_the_docker_hostname(self):
        self.assertEqual(photo_url(self.PATH), f"http://127.0.0.1:8002{self.PATH}")

    @override_settings(DEBUG=True, MARKETPLACE_PUBLIC_URL="http://marketplace:8000")
    def test_never_returns_a_docker_hostname(self):
        self.assertIsNone(photo_url(self.PATH))

    @override_settings(DEBUG=True, MARKETPLACE_PUBLIC_URL="http://127.0.0.1:8002")
    def test_rejects_external_and_non_media_paths(self):
        for path in ("https://evil.example/a.jpg", "//evil.example/a.jpg", "/admin/", "", None):
            with self.subTest(path=path):
                self.assertIsNone(photo_url(path))


class ListingMessagesTests(WebAuthTestCase):
    def setUp(self):
        self.sign_in_session()

    def test_new_offer_starts_with_the_shop_address(self):
        with patch_service("my_shop", return_value=(200, COMPLETE_SHOP)):
            response = self.client.get(reverse("create-listing"))
        self.assertContains(response, f'value="{COMPLETE_SHOP["address"]}"')

    def test_new_offer_form_still_opens_when_marketplace_is_down(self):
        with patch_service("my_shop", side_effect=ServiceUnavailable("marketplace")):
            self.assertEqual(self.client.get(reverse("create-listing")).status_code, 200)

    def test_field_errors_use_the_form_labels(self):
        with patch_service("create_listing", return_value=(400, {"discounted_price": ["Too high."]})):
            response = self.client.post(reverse("create-listing"), {"title": "Box"})
        self.assertContains(response, "Discounted price:", status_code=400)
        self.assertNotContains(response, "discounted_price:", status_code=400)

    def test_refused_photo_explains_why(self):
        photo = SimpleUploadedFile("a.png", b"not really", content_type="image/png")
        with patch_service("create_listing", return_value=(201, {"id": 7})), patch_service(
            "upload_listing_photo", return_value=(400, {"photo": ["This file is not a valid image."]})
        ), patch_service("my_shop", return_value=(200, COMPLETE_SHOP)), patch_service(
            "my_listings", return_value=(200, [])
        ):
            response = self.client.post(reverse("create-listing"), {"title": "Box", "photo": photo}, follow=True)
        self.assertContains(response, "This file is not a valid image.")

    def test_publish_refusal_names_what_is_missing(self):
        refusal = {"detail": ["x"], "fields": {"pickup_address": ["This field is required."]}}
        with patch_service("publish_listing", return_value=(400, refusal)), patch_service(
            "my_shop", return_value=(200, COMPLETE_SHOP)
        ), patch_service("my_listings", return_value=(200, [])):
            response = self.client.post(reverse("publish-listing", args=[7]), follow=True)
        self.assertContains(response, "Pickup address: This field is required")

    def test_another_shops_offer_gets_a_readable_404(self):
        with patch_service("my_listings", return_value=(200, [])):
            response = self.client.get(reverse("edit-listing", args=[7]))
        self.assertContains(response, "couldn&#x27;t find this offer in your shop", status_code=404)


class WorkspaceVisibilityTests(WebAuthTestCase):
    def workspace(self, shop=COMPLETE_SHOP, **listing):
        base = {
            "id": 1, "title": "Box", "status": "PUBLISHED", "total_quantity": 5, "free_quantity": 5,
            "pickup_address": "12 Bliss Street", "photo_display": None,
            "pickup_start": (timezone.now() + timedelta(hours=1)).isoformat(),
            "pickup_end": (timezone.now() + timedelta(hours=2)).isoformat(),
        }
        self.sign_in_session()
        with patch_service("my_shop", return_value=(200, shop)), patch_service(
            "my_listings", return_value=(200, [{**base, **listing}])
        ):
            return self.client.get(reverse("workspace"))

    APPROVED = {**COMPLETE_SHOP, "approval_status": "APPROVED"}

    def test_live_offer_links_to_its_public_page(self):
        response = self.workspace(self.APPROVED)
        self.assertContains(response, "Customers can see this offer.")
        self.assertContains(response, reverse("offer-detail", args=[1]))
        self.assertContains(response, "12 Bliss Street")
        self.assertContains(response, "name=\"quantity_change\"")

    def test_sold_out_offer_explains_it_is_hidden(self):
        response = self.workspace(self.APPROVED, free_quantity=0)
        self.assertContains(response, "Hidden from customers: it has sold out.")
        self.assertContains(response, "name=\"quantity_change\"")

    def test_ended_offer_explains_it_is_hidden_and_has_no_stock_form(self):
        response = self.workspace(self.APPROVED, pickup_end=(timezone.now() - timedelta(hours=1)).isoformat())
        self.assertContains(response, "Hidden from customers: its pickup time has ended.")
        self.assertNotContains(response, "name=\"quantity_change\"")

    def test_rejected_shop_offer_explains_it_is_hidden(self):
        response = self.workspace({**COMPLETE_SHOP, "approval_status": "REJECTED"})
        self.assertContains(response, "Hidden from customers: your shop isn&#x27;t approved.")
        self.assertNotContains(response, "Customers can see this offer.")

    def test_approved_status_no_longer_says_publishing_is_coming_later(self):
        self.assertNotContains(self.workspace(self.APPROVED), "later release")
