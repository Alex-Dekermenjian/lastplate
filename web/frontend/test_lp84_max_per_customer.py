"""LP-84: a restaurant limits how many of an offer one customer can reserve.

The web side: the restaurant sets the limit when it creates or edits a draft;
customers see it on the offer page; the cart never goes above it; and when
Reservations refuses a checkout for being over the limit (counting earlier
orders), the cart shows why and comes down to what is still allowed. The
counting itself is tested in the Reservations service.
"""

from django.contrib.messages import get_messages
from django.urls import reverse

from .test_accounts import ApprovedShopMixin, WebAuthTestCase, patch_service
from .test_browse_offers import offer
from .test_edit_listing import DRAFT_LISTING
from .test_lp19_checkout import CheckoutTestCase, changed
from .test_lp62_cart import CROISSANTS, CartTestCase, offers
from .test_shop_profile import COMPLETE_SHOP

LIMITED = {42: {**CROISSANTS, "free_quantity": 4, "max_per_customer": 2}}


def shown(response):
    return [str(message) for message in get_messages(response.wsgi_request)]


class RestaurantFormTests(ApprovedShopMixin, WebAuthTestCase):

    POST = {
        "title": "Pastry Box", "description": "Fresh pastries", "category": "",
        "original_price": "10.00", "discounted_price": "5.00", "total_quantity": "3",
        "ingredients": "Flour", "allergens": "Wheat",
    }

    def setUp(self):
        self.sign_in_session()

    def test_both_kinds_of_offer_have_the_limit_box(self):
        for kind in ("ITEM", "LASTBAG"):
            with self.subTest(kind=kind):
                response = self.client.get(reverse("create-listing"), {"kind": kind})
                self.assertContains(response, 'name="max_per_customer"')
                self.assertContains(response, "Max per customer")

    def test_a_new_draft_is_saved_with_its_limit(self):
        with patch_service("create_listing", return_value=(201, {"id": 7})) as create:
            self.client.post(reverse("create-listing"), {**self.POST, "max_per_customer": "2"})

        self.assertEqual(create.call_args.args[1]["max_per_customer"], "2")

    def test_a_new_draft_without_a_limit_sends_none(self):
        with patch_service("create_listing", return_value=(201, {"id": 7})) as create:
            self.client.post(reverse("create-listing"), {**self.POST, "max_per_customer": ""})

        self.assertNotIn("max_per_customer", create.call_args.args[1])

    def test_the_limit_is_sent_for_a_lastbag_too(self):
        bag = {"kind": "LASTBAG", "typical_contents": "Bread", "original_price": "15", "discounted_price": "5",
               "allergens": "Gluten", "total_quantity": "3", "max_per_customer": "1"}
        with patch_service("create_listing", return_value=(201, {"id": 7})) as create:
            self.client.post(reverse("create-listing"), bag)

        self.assertEqual(create.call_args.args[1]["max_per_customer"], "1")

    def test_marketplaces_refusal_is_shown_with_the_boxs_label(self):
        refusal = {"max_per_customer": ["The limit per customer must be a whole number, 1 or more."]}
        with patch_service("create_listing", return_value=(400, refusal)):
            response = self.client.post(reverse("create-listing"), {**self.POST, "max_per_customer": "0"})

        self.assertContains(response, "Max per customer", status_code=400)
        self.assertContains(response, "whole number, 1 or more", status_code=400)
        self.assertContains(response, 'value="0"', status_code=400)  # what they typed stays in the box

    def test_editing_shows_the_saved_limit(self):
        with patch_service("my_listings", return_value=(200, [{**DRAFT_LISTING, "max_per_customer": 3}])):
            response = self.client.get(reverse("edit-listing", args=[42]))

        self.assertContains(response, 'name="max_per_customer"')
        self.assertContains(response, 'value="3"')

    def test_editing_can_change_the_limit(self):
        with patch_service("my_listings", return_value=(200, [DRAFT_LISTING])), \
                patch_service("edit_listing", return_value=(200, {"id": 42})) as edit:
            self.client.post(reverse("edit-listing", args=[42]), {**self.POST, "max_per_customer": "4"})

        self.assertEqual(edit.call_args.args[2]["max_per_customer"], "4")

    def test_clearing_the_box_removes_the_limit(self):
        with patch_service("my_listings", return_value=(200, [{**DRAFT_LISTING, "max_per_customer": 3}])), \
                patch_service("edit_listing", return_value=(200, {"id": 42})) as edit:
            self.client.post(reverse("edit-listing", args=[42]), {**self.POST, "max_per_customer": ""})

        # Sent as null: leaving it out would keep the old limit.
        self.assertIn("max_per_customer", edit.call_args.args[2])
        self.assertIsNone(edit.call_args.args[2]["max_per_customer"])

    def test_the_owners_offer_list_shows_the_limit(self):
        listing = {"id": 1, "title": "Croissant Box", "status": "DRAFT", "total_quantity": 5,
                   "discounted_price": "5.00", "max_per_customer": 2}
        with patch_service("my_shop", return_value=(200, COMPLETE_SHOP)), \
                patch_service("my_listings", return_value=(200, [listing])):
            response = self.client.get(reverse("workspace"))

        self.assertContains(response, "max 2 per customer")

    def test_no_limit_shows_nothing(self):
        listing = {"id": 1, "title": "Croissant Box", "status": "DRAFT", "total_quantity": 5,
                   "discounted_price": "5.00", "max_per_customer": None}
        with patch_service("my_shop", return_value=(200, COMPLETE_SHOP)), \
                patch_service("my_listings", return_value=(200, [listing])):
            response = self.client.get(reverse("workspace"))

        self.assertNotContains(response, "per customer")


class OfferPageTests(WebAuthTestCase):

    def view(self, **changes):
        with patch_service("listing_offer", return_value=(200, offer(**changes))):
            return self.client.get(reverse("offer-detail", args=[42]))

    def test_customers_see_the_limit(self):
        response = self.view(max_per_customer=2)

        self.assertContains(response, "<dt>Limit</dt>", html=False)
        self.assertContains(response, "2 per customer")

    def test_an_offer_without_a_limit_shows_none(self):
        self.assertNotContains(self.view(max_per_customer=None), "per customer")
        self.assertNotContains(self.view(), "per customer")


class CartLimitTests(CartTestCase):

    def setUp(self):
        from .test_lp56_login_signup import CUSTOMER
        self.sign_in_as(CUSTOMER)

    def test_add_stops_at_the_limit_even_with_more_in_stock(self):
        for _ in range(3):
            response = self.add(42, changes=LIMITED)

        self.assertEqual(self.lines(), {"42": 2})
        self.assertIn("Limit of 2 per customer for Croissant Box.", shown(response))

    def test_stock_still_wins_when_it_is_lower_than_the_limit(self):
        changes = {42: {**CROISSANTS, "free_quantity": 1, "max_per_customer": 5}}
        self.add(42, changes=changes)
        response = self.add(42, changes=changes)

        self.assertEqual(self.lines(), {"42": 1})
        self.assertIn("Only 1 left of Croissant Box.", shown(response))

    def test_the_plus_button_stops_at_the_limit(self):
        self.add(42, changes=LIMITED)
        self.update(42, "increase", changes=LIMITED)
        response = self.update(42, "increase", changes=LIMITED)

        self.assertEqual(self.lines(), {"42": 2})
        self.assertIn("Limit of 2 per customer.", shown(response))

    def test_an_offer_without_a_limit_is_as_before(self):
        for _ in range(3):
            self.add(42, changes={42: {**CROISSANTS, "free_quantity": 4}})

        self.assertEqual(self.lines(), {"42": 3})

    def test_a_cart_above_a_new_limit_is_brought_down(self):
        for _ in range(3):
            self.add(42, changes={42: {**CROISSANTS, "free_quantity": 4}})

        response = self.view(changes=LIMITED)

        self.assertEqual(self.lines(), {"42": 2})
        self.assertContains(response, "Limit of 2 per customer for Croissant Box; we updated your cart.")
        self.assertTrue(response.context["lines"][0]["at_most"])


class CheckoutLimitTests(CheckoutTestCase):
    """Reservations says the customer is over the limit (it counted their
    earlier orders too)."""

    def over(self, allowed, detail):
        return changed({"listing_id": 42, "title": "Croissant Box", "code": "over_limit", "detail": detail,
                        "allowed": allowed, "limit": 2})

    def test_the_line_shows_why_and_comes_down_to_what_is_allowed(self):
        self.add(42)  # 2 croissants in the cart

        response, _, key = self.reserve(self.over(1, "Limit of 2 per customer: you can reserve 1 more."))

        self.assertContains(response, "Limit of 2 per customer: you can reserve 1 more.")
        self.assertContains(response, "nothing was reserved")
        self.assertEqual(self.lines()["42"], 1)
        self.assertNotEqual(self.key_on_page(), key)  # the next Reserve is a new attempt

    def test_nothing_is_allowed_any_more(self):
        self.add(42)

        response, _, _ = self.reserve(self.over(0, "You have reached the limit of 2 per customer for this offer."))

        self.assertContains(response, "You have reached the limit of 2 per customer for this offer.")
        # The line stays so the customer sees the message and removes it.
        self.assertEqual(self.lines()["42"], 2)
