"""LP-66: the restaurant's logo as a round badge on its page, offer cards,
the offer page and the cart; the name's first letter when there is no logo."""

from django.test import TestCase, override_settings
from django.urls import reverse

from .test_accounts import patch_service
from .test_browse_offers import offer
from .test_lp39_restaurant_page import SHOP
from .test_lp56_login_signup import CUSTOMER
from .test_lp62_cart import CEDAR, CartTestCase

PUBLIC = "http://127.0.0.1:8002"
LOGO_PATH = "/media/shop_logos/logo.png"
LOGO_URL = PUBLIC + LOGO_PATH


@override_settings(DEBUG=True, MARKETPLACE_PUBLIC_URL=PUBLIC)
class RestaurantPageLogoTests(TestCase):
    url = reverse("restaurant-detail", args=[3])

    def page(self, shop):
        with patch_service("restaurant_page", return_value=(200, {"shop": shop, "offers": []})):
            return self.client.get(self.url)

    def test_the_logo_is_a_large_round_badge_next_to_the_name(self):
        response = self.page({**SHOP, "logo_display": LOGO_PATH})

        self.assertContains(response, "shop-logo--large")
        self.assertContains(response, f'src="{LOGO_URL}"')

    def test_without_a_logo_the_first_letter_is_shown(self):
        response = self.page({**SHOP, "logo_display": None})

        self.assertContains(response, '<span class="shop-logo__initial">C</span>', html=True)
        self.assertNotContains(response, "shop-logo__image")

    def test_an_outside_logo_address_is_never_used(self):
        response = self.page({**SHOP, "logo_display": "https://elsewhere.example/logo.png"})

        self.assertNotContains(response, "elsewhere.example")
        self.assertContains(response, "shop-logo__initial")


@override_settings(DEBUG=True, MARKETPLACE_PUBLIC_URL=PUBLIC)
class OfferLogoTests(TestCase):
    def test_offer_cards_show_the_small_logo(self):
        shop = {"id": 3, "name": "Cedar Bakery", "area": "Hamra", "logo_display": LOGO_PATH}
        with patch_service("browse_listings", return_value=(200, [offer(shop=shop)])):
            response = self.client.get(reverse("browse-offers"))

        self.assertContains(response, "shop-logo--small")
        self.assertContains(response, f'src="{LOGO_URL}"')

    def test_the_offer_page_shows_the_logo_or_the_initial(self):
        for logo, expected in ((LOGO_PATH, f'src="{LOGO_URL}"'), (None, "shop-logo__initial")):
            shop = {"id": 3, "name": "Cedar Bakery", "area": "Hamra", "logo_display": logo}
            with self.subTest(logo=logo), patch_service("listing_offer", return_value=(200, offer(shop=shop))):
                response = self.client.get(reverse("offer-detail", args=[42]))
                self.assertContains(response, expected)


@override_settings(DEBUG=True, MARKETPLACE_PUBLIC_URL=PUBLIC)
class CartLogoTests(CartTestCase):
    def setUp(self):
        self.sign_in_as(CUSTOMER)

    def test_the_cart_shows_the_restaurant_logo(self):
        with_logo = {**CEDAR, "logo_display": LOGO_PATH}
        changes = {42: offer(id=42, title="Croissant Box", discounted_price="4.50", free_quantity=2, shop=with_logo)}
        self.add(42, changes)

        response = self.view(changes)

        self.assertContains(response, f'src="{LOGO_URL}"')

    def test_a_cart_saved_before_logos_shows_the_initial(self):
        self.add(42)
        session = self.client.session
        cart = session["cart"]
        cart.pop("shop_logo", None)
        session["cart"] = cart
        session.save()

        response = self.view()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "shop-logo__initial")
