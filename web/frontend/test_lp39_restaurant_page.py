"""LP-39: the restaurant page — details, then the LastBag, then the items."""

from django.test import TestCase
from django.urls import reverse
from django.utils.html import escape

from .browse_views import UNAVAILABLE_MESSAGE
from .services import ServiceUnavailable
from .test_accounts import patch_service
from .test_browse_offers import offer

SHOP = {
    "id": 3, "name": "Cedar Bakery", "area": "Hamra", "public_contact": "+961 1 234 567",
    "address": "12 Bliss Street, Beirut", "category": "Bakery", "photo_display": None,
}
BAG = offer(id=50, title="Evening LastBag", kind="LASTBAG", typical_contents="Bread, croissants, a cake slice",
            original_price="15.00", discounted_price="5.00", free_quantity=2, allergens_display="Gluten, milk")
ITEM = offer(id=42, kind="ITEM")


class RestaurantPageTests(TestCase):
    url = reverse("restaurant-detail", args=[3])

    def get(self, result=(200, {"shop": SHOP, "offers": [BAG, ITEM]}), **kwargs):
        with patch_service("restaurant_page", return_value=result, **kwargs) as api:
            response = self.client.get(self.url)
        return response, api

    def test_shows_the_restaurant_details(self):
        response, api = self.get()

        api.assert_called_once_with(3)
        for text in ("Cedar Bakery", "12 Bliss Street, Beirut", "Hamra", "Bakery", 'href="tel:+9611234567"'):
            self.assertContains(response, text)

    def test_lastbag_comes_first_with_worth_and_yours_for(self):
        response, _ = self.get()
        content = response.content.decode()

        for text in ("worth $15.00", "yours for $5.00", "Bags left", "Bread, croissants, a cake slice"):
            self.assertContains(response, text)
        self.assertLess(content.index("Evening LastBag"), content.index("Croissant Box"))
        self.assertContains(response, reverse("offer-detail", args=[42]))

    def test_a_restaurant_with_nothing_left_says_so(self):
        response, _ = self.get((200, {"shop": SHOP, "offers": []}))
        self.assertContains(response, "has nothing left right now")

    def test_pending_or_rejected_restaurants_are_not_found(self):
        response, _ = self.get((404, {"detail": "This restaurant is not available."}))

        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "This restaurant isn't available", status_code=404)
        self.assertNotContains(response, "Cedar Bakery", status_code=404)

    def test_marketplace_down_or_odd_answers(self):
        response, _ = self.get(side_effect=ServiceUnavailable("marketplace"))
        self.assertContains(response, escape(UNAVAILABLE_MESSAGE), status_code=503)

        response, _ = self.get((200, {"offers": []}))
        self.assertEqual(response.status_code, 502)

    def test_no_sign_in_needed(self):
        response, _ = self.get()
        self.assertEqual(response.status_code, 200)


class RestaurantLinkTests(TestCase):
    def test_browse_and_offer_pages_link_to_the_restaurant(self):
        with_shop_id = offer(shop={**offer()["shop"], "id": 3})
        restaurant_url = reverse("restaurant-detail", args=[3])

        with patch_service("browse_listings", return_value=(200, [with_shop_id])):
            self.assertContains(self.client.get(reverse("browse-offers")), restaurant_url)
        with patch_service("listing_offer", return_value=(200, with_shop_id)):
            self.assertContains(self.client.get(reverse("offer-detail", args=[42])), restaurant_url)
