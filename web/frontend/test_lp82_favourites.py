"""LP-82: the heart on a restaurant page and the customer's Favourites page."""

from urllib.parse import urlencode

from django.urls import reverse
from django.utils.html import escape

from .favourite_views import HEART_UNAVAILABLE
from .services import ServiceUnavailable
from .test_accounts import TOKENS, patch_service
from .test_browse_offers import offer
from .test_lp39_restaurant_page import SHOP
from .test_lp56_login_signup import CUSTOMER, OPERATOR
from .test_lp57_console import ADMIN
from .test_lp62_cart import CartTestCase

CEDAR_CARD = {"id": 3, "name": "Cedar Bakery", "area": "Hamra", "public_contact": "+961 1 234 567",
              "category": "Bakery", "photo_display": None, "logo_display": None, "branch_count": 1}
TODAY_OFFER = offer(id=42, title="Croissant Box", shop=CEDAR_CARD)


class FavouritesTestCase(CartTestCase):
    restaurant_url = reverse("restaurant-detail", args=[3])
    toggle_url = reverse("favourite-toggle", args=[3])
    page_url = reverse("favourites")

    def open_restaurant(self, favourites=(200, {"shops": []}), **kwargs):
        with patch_service("restaurant_page", return_value=(200, {"shop": SHOP, "offers": []})), \
                patch_service("favourite_shops", return_value=favourites, **kwargs) as api:
            return self.client.get(self.restaurant_url), api

    def press_heart(self, result=(200, {"shop_id": 3, "favourite": True}), **kwargs):
        with patch_service("toggle_favourite", return_value=result, **kwargs) as api:
            return self.client.post(self.toggle_url), api

    def open_page(self, result=(200, {"has_favourites": True, "offers": [TODAY_OFFER]}), **kwargs):
        with patch_service("favourite_offers", return_value=result, **kwargs) as api:
            return self.client.get(self.page_url), api


class HeartTests(FavouritesTestCase):
    def test_a_customer_sees_an_empty_heart_until_they_save_it(self):
        self.sign_in_as(CUSTOMER)

        response, api = self.open_restaurant()
        api.assert_called_once_with(TOKENS["access"])
        self.assertContains(response, f'action="{self.toggle_url}"')
        self.assertContains(response, 'aria-pressed="false"')

        response, _ = self.open_restaurant((200, {"shops": [CEDAR_CARD]}))
        self.assertContains(response, 'aria-pressed="true"')

    def test_pressing_the_heart_twice_saves_then_removes(self):
        self.sign_in_as(CUSTOMER)

        response, api = self.press_heart()
        api.assert_called_once_with(TOKENS["access"], 3)
        self.assertRedirects(response, self.restaurant_url, fetch_redirect_response=False)
        response, _ = self.open_restaurant((200, {"shops": [CEDAR_CARD]}))
        self.assertContains(response, "Saved to your favourites.")
        self.assertContains(response, 'aria-pressed="true"')

        self.press_heart((200, {"shop_id": 3, "favourite": False}))
        response, _ = self.open_restaurant()
        self.assertContains(response, "Removed from your favourites.")
        self.assertContains(response, 'aria-pressed="false"')

    def test_a_signed_out_visitor_sees_the_heart_and_is_sent_to_log_in(self):
        response, api = self.open_restaurant()
        api.assert_not_called()
        self.assertContains(response, 'aria-pressed="false"')

        response, api = self.press_heart()
        api.assert_not_called()
        self.assertRedirects(response, f"{reverse('login')}?{urlencode({'next': self.toggle_url})}",
                             fetch_redirect_response=False)

    def test_after_logging_in_the_visitor_comes_back_to_the_restaurant(self):
        self.sign_in_as(CUSTOMER)
        with patch_service("toggle_favourite") as api:
            response = self.client.get(self.toggle_url)
        api.assert_not_called()  # a GET never changes anything
        self.assertRedirects(response, self.restaurant_url, fetch_redirect_response=False)

    def test_restaurants_and_the_admin_never_see_or_use_the_heart(self):
        for account in (OPERATOR, ADMIN):
            with self.subTest(role=account["role"]):
                self.sign_in_as(account)
                response, api = self.open_restaurant()
                api.assert_not_called()
                self.assertNotContains(response, self.toggle_url)

                response, api = self.press_heart()
                api.assert_not_called()
                self.assertEqual(response.status_code, 302)

    def test_problems_never_break_the_restaurant_page(self):
        self.sign_in_as(CUSTOMER)
        response, _ = self.open_restaurant(side_effect=ServiceUnavailable("marketplace"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'aria-pressed="false"')

        response, _ = self.press_heart(side_effect=ServiceUnavailable("marketplace"))
        self.assertRedirects(response, self.restaurant_url, fetch_redirect_response=False)
        response, _ = self.open_restaurant()
        self.assertContains(response, escape(HEART_UNAVAILABLE))

        self.press_heart((404, {"code": "not_found"}))
        response, _ = self.open_restaurant()
        self.assertContains(response, "so it can&#x27;t be saved.")


class FavouritesPageTests(FavouritesTestCase):
    def test_shows_the_offers_marketplace_says_are_available_today(self):
        self.sign_in_as(CUSTOMER)
        response, api = self.open_page()

        api.assert_called_once_with(TOKENS["access"])
        self.assertContains(response, "Croissant Box")
        self.assertContains(response, "1 offer available today")
        self.assertContains(response, reverse("offer-detail", args=[42]))
        self.assertContains(response, reverse("restaurant-detail", args=[3]))

    def test_friendly_message_with_no_favourites(self):
        self.sign_in_as(CUSTOMER)
        response, _ = self.open_page((200, {"has_favourites": False, "offers": []}))
        self.assertContains(response, "You haven't saved any restaurants yet.")
        self.assertContains(response, reverse("browse-offers"))
        self.assertNotContains(response, "offer-grid")

    def test_friendly_message_when_favourites_have_nothing_today(self):
        self.sign_in_as(CUSTOMER)
        response, _ = self.open_page((200, {"has_favourites": True, "offers": []}))
        self.assertContains(response, "Your favourite restaurants have nothing left today.")
        self.assertNotContains(response, "offer-grid")

    def test_marketplace_down_or_odd_answer(self):
        self.sign_in_as(CUSTOMER)
        response, _ = self.open_page(side_effect=ServiceUnavailable("marketplace"))
        self.assertContains(response, "Can&#x27;t load your favourites", status_code=503)

        response, _ = self.open_page((200, {"oops": True}))
        self.assertEqual(response.status_code, 502)

    def test_customers_only(self):
        response, api = self.open_page()
        api.assert_not_called()
        self.assertRedirects(response, f"{reverse('login')}?{urlencode({'next': self.page_url})}",
                             fetch_redirect_response=False)

        for account in (OPERATOR, ADMIN):
            with self.subTest(role=account["role"]):
                self.sign_in_as(account)
                response, api = self.open_page()
                api.assert_not_called()
                self.assertEqual(response.status_code, 302)


class NavigationTests(FavouritesTestCase):
    def test_reachable_from_profile_and_the_home_page(self):
        self.sign_in_as(CUSTOMER)
        self.assertContains(self.client.get(reverse("my-account")), self.page_url)
        with patch_service("browse_listings", return_value=(200, [])):
            self.assertContains(self.client.get(reverse("home")), self.page_url)

    def test_the_bottom_bar_keeps_its_five_tabs(self):
        self.sign_in_as(CUSTOMER)
        content = self.client.get(reverse("my-account")).content.decode()
        start = content.index('class="bottom-bar-customer"')
        bar = content[start:content.index("</nav>", start)]
        self.assertEqual(bar.count("<a "), 5)
