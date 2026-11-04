
"""Tests for creating food listing drafts (LP-10)."""

from django.urls import reverse

from .test_accounts import WebAuthTestCase, patch_service


class CreateListingPageTests(WebAuthTestCase):

    def setUp(self):
        self.url = reverse("create-listing")
        self.sign_in_session()

    def test_create_listing_page_opens(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Create a food offer")
        self.assertContains(response, "Save draft")

    def test_successful_draft_creation(self):
        listing_data = {
            "title": "Surprise Pastry Box",
            "description": "Fresh bakery leftovers",
            "category": "Bakery",
            "original_price": "10.00",
            "discounted_price": "5.00",
            "total_quantity": "3",
            "pickup_address": "12 Bliss Street, Beirut",
            "pickup_start": "2026-11-05T17:00",
            "pickup_end": "2026-11-05T19:00",
            "ingredients": "Flour, butter",
            "allergens": "Wheat, milk",
        }

        with patch_service(
            "create_listing",
            return_value=(201, {"id": 1, "status": "DRAFT"}),
        ) as create_listing:
            response = self.client.post(self.url, listing_data)

        self.assertRedirects(
            response,
            reverse("workspace"),
            fetch_redirect_response=False,
        )

        create_listing.assert_called_once_with(
            "access-token-1",
            listing_data,
        )
