"""LP-83: a customer reserves straight from an offer's page.

One test class per rule of the task. The checkout itself (rechecks, holds,
2-order limit) is LP-19's and tested in the Reservations service; here the
web sends one line through services.create_reservation and shows the answer.
"""

import uuid
from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from .offer_reserve import NOTICE_KEY, SEEN_KEY, TRY_AGAIN
from .services import ServiceUnavailable
from .test_accounts import patch_service
from .test_browse_offers import offer
from .test_lp39_restaurant_page import SHOP
from .test_lp56_login_signup import CUSTOMER, OPERATOR
from .test_lp62_cart import CartTestCase

SOON = timezone.now() + timedelta(hours=2)
DONUTS = offer(id=42, title="Glazed donuts", discounted_price="1.50", free_quantity=3,
               pickup_start=SOON.isoformat(), pickup_end=(SOON + timedelta(hours=3)).isoformat(),
               shop={"id": 3, "name": "Cedar Bakery", "area": "Hamra", "public_contact": "+961 1 234 567"})
ORDER = {"reservation_ref": "0b8d1f0e-6c1e-4a59-9a43-6d3f8d2b0c11", "status": "CONFIRMED",
         "status_label": "Reserved", "pickup_code": "TM9AY3", "pick_up_by": "2026-11-19T17:15:00+00:00",
         "total_cash": "3.00", "shop_id": 3, "branch_id": 7, "created_at": "2026-11-19T16:15:00+00:00",
         # LP-78: saved with the order at checkout.
         "shop_name": "Cedar Bakery", "branch_name": "", "pickup_address": "12 Bliss Street, Beirut",
         "shop_phone": "+961 1 234 567", "cancelled_by": None, "collected_at": None,
         "lines": [{"listing_id": 42, "title": "Glazed donuts", "quantity": 2, "unit_price": "1.50",
                    "pickup_start": "2026-11-19T16:00:00+00:00", "pickup_end": "2026-11-19T19:00:00+00:00"}]}
ORDER_PAGE = reverse("order-detail", args=[ORDER["reservation_ref"]])
ADMIN = {**CUSTOMER, "id": 99, "email": "admin@example.test", "role": "ADMIN"}


def changed(**problem):
    return 409, {"code": "cart_changed", "detail": "Something in your cart changed. Nothing was reserved.",
                 "problems": [{"listing_id": 42, **problem}]}


class OfferPageTestCase(CartTestCase):
    url = reverse("offer-detail", args=[42])
    reserve_url = reverse("offer-reserve", args=[42])

    def setUp(self):
        self.sign_in_as(CUSTOMER)

    def page(self, data=DONUTS, status=200):
        with patch_service("listing_offer", return_value=(status, data if status == 200 else {"detail": "x"})):
            return self.client.get(self.url)

    def key(self, response):
        return response.context["request_key"]

    def press(self, answer=(201, ORDER), quantity=1, key=None, after=DONUTS, after_status=200):
        """Load the page, press Reserve, and follow back to the page."""
        key = key or self.key(self.page())
        side_effect = answer if isinstance(answer, Exception) else None
        with patch_service("create_reservation", return_value=None if side_effect else answer,
                           side_effect=side_effect) as call:
            self.client.post(self.reserve_url, {"request_key": key, "quantity": quantity})
        return self.page(after, after_status), call, key


class CustomerSeesQuantityAndReserveTests(OfferPageTestCase):
    """A signed-in customer sees a quantity (1 to how many are left) and
    Reserve at the bottom of the offer page."""

    def test_quantity_and_reserve_are_at_the_bottom(self):
        html = self.page().content.decode()
        self.assertIn('name="quantity"', html)
        self.assertIn(f'action="{self.reserve_url}"', html)
        for detail in ("$1.50", "Ingredients", "Allergens", "Fresh pastries"):
            self.assertLess(html.index(detail), html.index('name="quantity"'), detail)

    def test_quantity_goes_from_1_to_how_many_are_left(self):
        response = self.page()
        self.assertEqual(list(response.context["quantities"]), [1, 2, 3])
        self.assertContains(response, '<option value="3">3</option>')
        self.assertNotContains(response, '<option value="4">')

    def test_a_large_stock_is_capped_at_what_one_order_may_hold(self):
        self.assertEqual(list(self.page({**DONUTS, "free_quantity": 500}).context["quantities"])[-1], 100)

    def test_the_page_carries_a_checkout_key(self):
        key = self.key(self.page())
        self.assertEqual(str(uuid.UUID(key)), key)
        self.assertEqual(self.key(self.page()), key)  # same key while nothing was ordered


class ReserveHereTests(OfferPageTestCase):
    """Pressing Reserve places the order right there with the LP-19 checkout
    (one line), and shows the code, pick-up-by time and cash total."""

    def test_reserve_sends_one_line_with_the_page_key(self):
        _, call, key = self.press(quantity=2)
        access, sent_key, lines = call.call_args.args
        self.assertEqual(sent_key, key)
        self.assertEqual(lines, [{"listing_id": 42, "quantity": 2, "expected_unit_price": "1.50"}])

    def test_reserve_lands_on_the_order_page_with_the_code_and_total(self):
        # LP-78: was the one-time "Order placed!" message; now the order page.
        key = self.key(self.page())
        with patch_service("create_reservation", return_value=(201, ORDER)), \
                patch_service("get_order", return_value=(200, ORDER)):
            response = self.client.post(self.reserve_url, {"request_key": key, "quantity": 2}, follow=True)
        self.assertEqual(response.redirect_chain[-1][0], ORDER_PAGE)
        for text in ("TM9AY3", "Pay $3.00 in cash at pickup.", "Pick up by <strong>19:15</strong>",
                     "Show this code at the counter"):
            self.assertContains(response, text, html=False)
        self.assertNotIn(NOTICE_KEY, self.client.session)

    def test_the_left_number_drops(self):
        response, _, _ = self.press(quantity=2, after={**DONUTS, "free_quantity": 1})
        self.assertContains(response, "1 left")
        self.assertEqual(list(response.context["quantities"]), [1])

    def test_the_last_ones_ordered_here_go_to_the_order_page_and_the_offer_says_sold_out(self):
        key = self.key(self.page())
        with patch_service("create_reservation", return_value=(201, ORDER)):
            response = self.client.post(self.reserve_url, {"request_key": key, "quantity": 3})
        self.assertRedirects(response, ORDER_PAGE, fetch_redirect_response=False)
        later = self.page(status=404)  # back on the offer page: none left
        self.assertContains(later, "<strong>Sold out</strong>", status_code=404)

    def test_the_cart_is_left_alone(self):
        self.add(43)
        self.press()
        self.assertEqual(self.lines(), {"43": 1})

    def test_reserve_is_post_only(self):
        self.assertEqual(self.client.get(self.reserve_url).status_code, 405)


class OthersOnlySeeDetailsTests(OfferPageTestCase):
    """Signed-out visitors, shop owners and admins see the details only."""

    def assert_details_only(self, response):
        self.assertContains(response, "Glazed donuts")
        self.assertContains(response, "3 left")
        self.assertNotContains(response, 'name="quantity"')
        self.assertNotContains(response, f'action="{self.reserve_url}"')
        self.assertNotContains(response, "Sold out")

    def test_signed_out_visitor(self):
        self.client = self.client_class()
        self.assert_details_only(self.page())

    def test_shop_owner_and_admin(self):
        for account in (OPERATOR, ADMIN):
            self.client = self.client_class()
            self.sign_in_as(account)
            self.assert_details_only(self.page())

    def test_they_cannot_reserve_by_posting_either(self):
        self.client = self.client_class()
        with patch_service("create_reservation") as call:
            response = self.client.post(self.reserve_url, {"request_key": str(uuid.uuid4()), "quantity": 1})
        self.assertTrue(response["Location"].startswith(reverse("login")))
        self.sign_in_as(OPERATOR)
        with patch_service("create_reservation") as call:
            self.client.post(self.reserve_url, {"request_key": str(uuid.uuid4()), "quantity": 1})
        call.assert_not_called()


class DoubleClickAndProblemsTests(OfferPageTestCase):
    """Double click = one order; problems show a clear message on the page."""

    def test_double_click_sends_the_same_key(self):
        key = self.key(self.page())
        _, first, _ = self.press(key=key)
        _, second, _ = self.press(answer=(200, ORDER), key=key)
        self.assertEqual(first.call_args.args[1], second.call_args.args[1])

    def test_after_an_order_the_next_reserve_is_a_new_order(self):
        response, _, key = self.press()
        self.assertNotEqual(self.key(response), key)

    def test_only_1_left(self):
        response, _, key = self.press(changed(code="only_n_left", detail="Only 1 left.", free_quantity=1), quantity=2,
                                      after={**DONUTS, "free_quantity": 1})
        self.assertContains(response, "Only 1 left. Nothing was reserved.")
        self.assertNotEqual(self.key(response), key)

    def test_sold_out(self):
        response, _, _ = self.press(changed(code="only_n_left", detail="Sold out.", free_quantity=0),
                                    after_status=404)
        self.assertContains(response, "Sold out. Nothing was reserved.", status_code=404)

    def test_pickup_ended(self):
        ended = {**DONUTS, "pickup_end": (timezone.now() - timedelta(minutes=1)).isoformat()}
        key = self.key(self.page())
        session = self.client.session
        session[SEEN_KEY]["42"]["ends"] = ended["pickup_end"]
        session.save()
        response, _, _ = self.press(changed(code="not_available", detail="This offer is no longer available."),
                                    key=key, after_status=404)
        self.assertContains(response, "Pickup ended. Nothing was reserved.", status_code=404)

    def test_two_orders_waiting(self):
        detail = "You already have 2 orders waiting for pickup. Pick one up or cancel one before ordering again."
        response, _, _ = self.press((409, {"code": "too_many_orders", "detail": detail}))
        self.assertContains(response, "You already have 2 orders waiting for pickup.")

    def test_press_reserve_again_when_marketplace_gives_no_answer(self):
        response, _, key = self.press((202, {"code": "try_again",
                                             "detail": "We couldn't confirm your order yet. Please press Reserve again."}))
        self.assertContains(response, "Please press Reserve again.")
        self.assertEqual(self.key(response), key)  # pressing again continues the same order

    def test_press_reserve_again_when_reservations_is_unreachable(self):
        response, _, key = self.press(ServiceUnavailable("reservations"))
        self.assertContains(response, "Nothing was reserved yet. Please press Reserve again.")
        self.assertIn("Please press Reserve again.", TRY_AGAIN)
        self.assertEqual(self.key(response), key)

    def test_press_reserve_again_shows_even_when_the_offer_cannot_load(self):
        key = self.key(self.page())
        with patch_service("create_reservation", side_effect=ServiceUnavailable("reservations")):
            self.client.post(self.reserve_url, {"request_key": key, "quantity": 1})
        with patch_service("listing_offer", side_effect=ServiceUnavailable("marketplace")):
            response = self.client.get(self.url)
        self.assertContains(response, "Please press Reserve again.", status_code=503)
        self.assertEqual(self.key(self.page()), key)  # back up: the same order continues

    def test_a_bad_quantity_or_old_page_does_nothing(self):
        key = self.key(self.page())
        with patch_service("create_reservation") as call:
            self.client.post(self.reserve_url, {"request_key": key, "quantity": 0})
            self.client.post(self.reserve_url, {"request_key": "old", "quantity": 1})
        call.assert_not_called()


class SoldOutOrEndedTests(OfferPageTestCase):
    """Sold out or pickup over: "Sold out" / "Pickup ended" instead of the button."""

    def assert_closed(self, response, text, status_code=200):
        self.assertContains(response, f"<strong>{text}</strong>", status_code=status_code)
        self.assertNotContains(response, 'name="quantity"', status_code=status_code)

    def test_sold_out_instead_of_the_button(self):
        self.assert_closed(self.page({**DONUTS, "free_quantity": 0}), "Sold out")

    def test_pickup_ended_instead_of_the_button(self):
        ended = {**DONUTS, "pickup_end": (timezone.now() - timedelta(minutes=1)).isoformat()}
        self.assert_closed(self.page(ended), "Pickup ended")

    def test_an_offer_seen_earlier_that_is_now_gone(self):
        self.page()  # seen while on sale
        self.assert_closed(self.page(status=404), "Sold out", status_code=404)
        session = self.client.session
        session[SEEN_KEY]["42"]["ends"] = (timezone.now() - timedelta(minutes=1)).isoformat()
        session.save()
        self.assert_closed(self.page(status=404), "Pickup ended", status_code=404)

    def test_an_offer_never_seen_keeps_the_general_message(self):
        response = self.page(status=404)
        self.assertContains(response, "This offer isn't available", status_code=404)


class NoReserveOnListsTests(OfferPageTestCase):
    """No Reserve buttons on the home page or the offer list cards."""

    def test_home_and_offers_list(self):
        for url in (reverse("home"), reverse("browse-offers")):
            with patch_service("browse_listings", return_value=(200, [DONUTS])):
                response = self.client.get(url)
            self.assertEqual(response.status_code, 200, url)
            html = response.content.decode()
            self.assertIn("Glazed donuts", html, url)
            self.assertNotIn('name="quantity"', html, url)
            self.assertNotIn("/reserve/", html, url)
            self.assertNotIn(">Reserve<", html, url)


class RestaurantPageAndCartStillWorkTests(OfferPageTestCase):
    """The restaurant page and the cart are unchanged."""

    def test_restaurant_page_still_has_add_to_cart(self):
        with patch_service("restaurant_page", return_value=(200, {"shop": SHOP, "offers": [DONUTS]})):
            response = self.client.get(reverse("restaurant-detail", args=[3]))
        self.assertContains(response, reverse("cart-add", args=[42]))
        self.assertNotContains(response, 'name="quantity"')

    def test_the_cart_still_reserves(self):
        self.add(42)
        response = self.view()
        self.assertContains(response, 'action="/cart/checkout/"')
