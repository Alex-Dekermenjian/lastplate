"""LP-62: a customer adds food from one restaurant to a session cart, changes
quantities and sees the total. Nothing is reserved; stock is re-checked."""

from decimal import Decimal
from urllib.parse import urlencode

from django.urls import reverse

from . import account_views, tokens
from .cart import CART_KEY
from .services import ServiceUnavailable
from .test_accounts import TOKENS, WebAuthTestCase, patch_service
from .test_browse_offers import offer
from .test_lp39_restaurant_page import SHOP
from .test_lp56_login_signup import CUSTOMER, OPERATOR

CEDAR = {"id": 3, "name": "Cedar Bakery", "area": "Hamra", "public_contact": "+961 1 234 567"}
OLIVE = {"id": 4, "name": "Olive Tree", "area": "Achrafieh", "public_contact": "+961 1 765 432"}
CROISSANTS = offer(id=42, title="Croissant Box", discounted_price="4.50", free_quantity=2, shop=CEDAR)
BREAD = offer(id=43, title="Bread Bag", discounted_price="3.00", free_quantity=5, shop=CEDAR)
SALAD = offer(id=60, title="Salad Bowl", discounted_price="6.00", free_quantity=4, shop=OLIVE)
OFFERS = {42: CROISSANTS, 43: BREAD, 60: SALAD}


def offers(changes=None):
    """A fake listing_offer: 404 for anything missing or removed."""
    table = {**OFFERS, **(changes or {})}

    def listing_offer(listing_id):
        data = table.get(int(listing_id))
        return (200, data) if data else (404, {"detail": "This offer is not available."})

    return listing_offer


class CartTestCase(WebAuthTestCase):
    back = reverse("restaurant-detail", args=[3])

    def sign_in_as(self, account):
        session = self.client.session
        session[tokens.ACCESS_TOKEN_KEY] = TOKENS["access"]
        session[tokens.REFRESH_TOKEN_KEY] = TOKENS["refresh"]
        session[account_views.ACCOUNT_KEY] = {k: account[k] for k in ("id", "email", "display_name", "role")}
        session.save()

    def add(self, listing_id, changes=None, **data):
        url = f"{reverse('cart-add', args=[listing_id])}?{urlencode({'back': self.back})}"
        with patch_service("listing_offer", side_effect=offers(changes)):
            return self.client.post(url, data)

    def update(self, listing_id, action, changes=None):
        with patch_service("listing_offer", side_effect=offers(changes)):
            return self.client.post(reverse("cart-update", args=[listing_id]), {"action": action})

    def view(self, changes=None):
        with patch_service("listing_offer", side_effect=offers(changes)):
            return self.client.get(reverse("cart"))

    def lines(self):
        return self.client.session.get(CART_KEY, {}).get("lines", {})


class AddToCartTests(CartTestCase):
    def setUp(self):
        self.sign_in_as(CUSTOMER)

    def test_add_puts_one_in_the_cart_and_goes_back(self):
        response = self.add(42)

        self.assertRedirects(response, self.back, fetch_redirect_response=False)
        self.assertEqual(self.lines(), {"42": 1})
        self.assertEqual(self.client.session[CART_KEY]["shop_id"], 3)

    def test_quantity_never_goes_above_what_is_left(self):
        for _ in range(3):
            self.add(42)
        self.assertEqual(self.lines(), {"42": 2})

    def test_sold_out_or_removed_food_is_not_added(self):
        self.add(42, changes={42: None})
        self.assertEqual(self.lines(), {})

    def test_another_restaurant_asks_before_clearing(self):
        self.add(42)

        response = self.add(60)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Start a new cart?")
        self.assertContains(response, "Your cart from Cedar Bakery will be cleared.")
        self.assertContains(response, "Keep my cart")
        self.assertEqual(self.lines(), {"42": 1})

        self.add(60, start_new="1")
        self.assertEqual(self.lines(), {"60": 1})
        self.assertEqual(self.client.session[CART_KEY]["shop_name"], "Olive Tree")

    def test_back_cannot_send_you_to_another_site(self):
        self.back = "https://evil.example/"
        self.assertRedirects(self.add(42), reverse("cart"), fetch_redirect_response=False)

    def test_food_service_down(self):
        url = reverse("cart-add", args=[42])
        with patch_service("listing_offer", side_effect=ServiceUnavailable("marketplace")):
            self.client.post(url)
        self.assertEqual(self.lines(), {})


class CartPageTests(CartTestCase):
    def setUp(self):
        self.sign_in_as(CUSTOMER)
        self.add(42)
        self.add(43)
        self.add(43)

    def test_lists_lines_with_prices_and_the_cash_total(self):
        response = self.view()

        for text in ("Croissant Box", "Bread Bag", "$4.50", "$6.00", "Total, paid in cash at pickup",
                     "$10.50", "Reserve"):
            self.assertContains(response, text)
        self.assertEqual(response.context["total"], Decimal("10.50"))

    def test_plus_minus_and_remove(self):
        self.update(42, "increase")
        self.assertEqual(self.lines()["42"], 2)
        self.update(42, "increase")  # only 2 left
        self.assertEqual(self.lines()["42"], 2)
        self.update(42, "decrease")
        self.update(42, "decrease")  # never below 1
        self.assertEqual(self.lines()["42"], 1)
        self.update(42, "remove")
        self.assertEqual(self.lines(), {"43": 2})

    def test_sold_out_line_is_greyed_out_and_not_counted(self):
        response = self.view(changes={42: None})

        self.assertContains(response, "No longer available")
        self.assertContains(response, "cart-line--gone")
        self.assertEqual(response.context["total"], Decimal("6.00"))

    def test_a_line_above_what_is_left_is_lowered(self):
        response = self.view(changes={43: {**BREAD, "free_quantity": 1}})

        self.assertEqual(response.context["total"], Decimal("7.50"))
        self.assertEqual(self.lines()["43"], 1)

    def test_restaurant_page_shows_the_cart_bar(self):
        with patch_service("restaurant_page", return_value=(200, {"shop": SHOP, "offers": [CROISSANTS]})):
            response = self.client.get(self.back)

        self.assertContains(response, "3 items · $10.50")
        self.assertContains(response, reverse("cart"))
        self.assertContains(response, reverse("cart-add", args=[42]))


class WhoCanUseTheCartTests(CartTestCase):
    def test_signed_out_visitors_log_in_and_come_back(self):
        url = f"{reverse('cart-add', args=[42])}?{urlencode({'back': self.back})}"
        with patch_service("listing_offer") as api:
            response = self.client.post(url)

        self.assertRedirects(response, f"{reverse('login')}?{urlencode({'next': url})}",
                             fetch_redirect_response=False)
        api.assert_not_called()

        # After login the browser opens the same address with GET.
        self.sign_in_as(CUSTOMER)
        self.assertRedirects(self.client.get(url), self.back, fetch_redirect_response=False)
        self.assertEqual(self.lines(), {})

    def test_restaurants_cannot_use_the_cart_even_with_a_forged_request(self):
        self.sign_in_as(OPERATOR)
        for response in (self.add(42), self.view(), self.update(42, "increase")):
            self.assertRedirects(response, reverse("workspace"), fetch_redirect_response=False)
        self.assertEqual(self.lines(), {})

    def test_restaurants_see_no_add_buttons(self):
        self.sign_in_as(OPERATOR)
        with patch_service("restaurant_page", return_value=(200, {"shop": SHOP, "offers": [CROISSANTS]})):
            response = self.client.get(self.back)
        self.assertNotContains(response, reverse("cart-add", args=[42]))

    def test_signed_out_visitors_see_add_buttons(self):
        with patch_service("restaurant_page", return_value=(200, {"shop": SHOP, "offers": [CROISSANTS]})):
            response = self.client.get(self.back)
        self.assertContains(response, reverse("cart-add", args=[42]))

    def test_logging_out_empties_the_cart(self):
        self.sign_in_as(CUSTOMER)
        self.add(42)
        with patch_service("sign_out", return_value=(200, {})):
            self.client.post(reverse("sign-out"))
        self.assertNotIn(CART_KEY, self.client.session)
