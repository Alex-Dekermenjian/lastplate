
from django.urls import reverse

from .services import ServiceUnavailable
from .test_accounts import WebAuthTestCase, patch_service
from .test_shop_profile import COMPLETE_SHOP


class WorkspaceListingTests(WebAuthTestCase):

    def setUp(self):
        self.url = reverse("workspace")
        self.sign_in_session()

        self.listing = {
            "id": 1,
            "title": "Croissant Box",
            "description": "Fresh pastries",
            "status": "DRAFT",
            "photo_display": None,
            "original_price": "10.00",
            "discounted_price": "5.00",
            "ingredients_display": "Flour, Butter",
            "allergens_display": "Milk, Wheat",
            "total_quantity": 5,
        }

    def test_listing_information_is_displayed(self):
        with patch_service(
            "my_shop",
            return_value=(200, COMPLETE_SHOP),
        ), patch_service(
            "my_listings",
            return_value=(200, [self.listing]),
        ):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Croissant Box")
        self.assertContains(response, "Fresh pastries")
        self.assertContains(response, "Flour, Butter")
        self.assertContains(response, "Milk, Wheat")
        self.assertContains(response, "5.00")

    def test_missing_photo_shows_placeholder(self):
        with patch_service(
            "my_shop",
            return_value=(200, COMPLETE_SHOP),
        ), patch_service(
            "my_listings",
            return_value=(200, [self.listing]),
        ):
            response = self.client.get(self.url)

        self.assertContains(response, "No photo available")

    def test_missing_allergen_information_shows_unknown(self):
        listing = {
            **self.listing,
            "ingredients_display": "Unknown",
            "allergens_display": "Unknown",
        }

        with patch_service(
            "my_shop",
            return_value=(200, COMPLETE_SHOP),
        ), patch_service(
            "my_listings",
            return_value=(200, [listing]),
        ):
            response = self.client.get(self.url)

        self.assertContains(response, "Unknown", count=2)

    def test_empty_list_shows_message(self):
        with patch_service(
            "my_shop",
            return_value=(200, COMPLETE_SHOP),
        ), patch_service(
            "my_listings",
            return_value=(200, []),
        ):
            response = self.client.get(self.url)

        self.assertContains(
            response,
            "You haven't created any food offers yet.",
        )

    def test_unavailable_listings_show_warning(self):
        with patch_service(
            "my_shop",
            return_value=(200, COMPLETE_SHOP),
        ), patch_service(
            "my_listings",
            side_effect=ServiceUnavailable("marketplace"),
        ):
            response = self.client.get(self.url)

        self.assertContains(
            response,
            "We couldn't load your food offers",
        )
        self.assertContains(response, "Cedar Crumb Bakery")
