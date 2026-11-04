
"""Tests for publishing food offers through the Web frontend."""

from django.urls import reverse

from .test_accounts import WebAuthTestCase, patch_service
from .test_shop_profile import COMPLETE_SHOP


class PublishListingTests(WebAuthTestCase):

    def setUp(self):
        self.url = reverse("publish-listing", args=[42])
        self.sign_in_session()

    def test_get_request_is_not_allowed(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 405)

    def test_successful_publication(self):
        with patch_service(
            "publish_listing",
            return_value=(200, {"id": 42, "status": "PUBLISHED"}),
        ) as publish_api:
            response = self.client.post(self.url)

        self.assertRedirects(
            response,
            reverse("workspace"),
            fetch_redirect_response=False,
        )
        publish_api.assert_called_once_with("access-token-1", 42)

    def test_unapproved_shop_cannot_publish(self):
        with patch_service(
            "publish_listing",
            return_value=(
                403,
                {"detail": "Your shop must be approved before publishing."},
            ),
        ):
            response = self.client.post(self.url)

        self.assertRedirects(
            response,
            reverse("workspace"),
            fetch_redirect_response=False,
        )

    def test_invalid_listing_cannot_publish(self):
        with patch_service(
            "publish_listing",
            return_value=(
                400,
                {"detail": "Pickup window is required."},
            ),
        ):
            response = self.client.post(self.url)

        self.assertRedirects(
            response,
            reverse("workspace"),
            fetch_redirect_response=False,
        )

    def test_publish_button_only_for_approved_shops(self):
        listing = {
            "id": 42,
            "title": "Pastry Box",
            "status": "DRAFT",
            "ingredients_display": "Unknown",
            "allergens_display": "Unknown",
        }

        with patch_service(
            "my_shop",
            return_value=(
                200,
                {
                    **COMPLETE_SHOP,
                    "approval_status": "APPROVED",
                },
            ),
        ), patch_service(
            "my_listings",
            return_value=(200, [listing]),
        ):
            response = self.client.get(reverse("workspace"))

        self.assertContains(response, "Publish offer")

        pending_shop = {
            **COMPLETE_SHOP,
            "approval_status": "PENDING",
        }

        with patch_service(
            "my_shop",
            return_value=(200, pending_shop),
        ), patch_service(
            "my_listings",
            return_value=(200, [listing]),
        ):
            response = self.client.get(reverse("workspace"))

        self.assertNotContains(response, "Publish offer")
