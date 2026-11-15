"""LP-19: the customer presses Reserve in the cart.

The web only sends the cart to Reservations and shows the answer; the
rechecks, holds and order rules are tested in the Reservations service.
"""

import uuid

from django.urls import reverse

from .cart import CART_KEY, TRY_AGAIN
from .services import ServiceUnavailable
from .test_accounts import patch_service
from .test_lp56_login_signup import CUSTOMER, OPERATOR
from .test_lp62_cart import BREAD, CROISSANTS, CartTestCase, offers

ORDER = {
    "reservation_ref": "0b8d1f0e-6c1e-4a59-9a43-6d3f8d2b0c11", "status": "CONFIRMED", "status_label": "Reserved",
    "pickup_code": "TM9AY3", "pick_up_by": "2026-11-19T17:15:00+00:00", "total_cash": "7.50",
    "shop_id": 3, "branch_id": 7, "created_at": "2026-11-19T16:15:00+00:00",
    # LP-78: saved with the order at checkout.
    "shop_name": "Cedar Bakery", "branch_name": "", "pickup_address": "12 Bliss Street, Beirut",
    "shop_phone": "+961 1 234 567", "cancelled_by": None, "collected_at": None,
    "lines": [{"listing_id": 42, "title": "Croissant Box", "quantity": 1, "unit_price": "4.50",
               "pickup_start": "2026-11-19T16:00:00+00:00", "pickup_end": "2026-11-19T19:00:00+00:00"},
              {"listing_id": 43, "title": "Bread Bag", "quantity": 1, "unit_price": "3.00",
               "pickup_start": "2026-11-19T16:00:00+00:00", "pickup_end": "2026-11-19T19:00:00+00:00"}],
}


def changed(*problems):
    return 409, {"code": "cart_changed", "detail": "Something in your cart changed. Nothing was reserved.",
                 "problems": list(problems)}


class CheckoutTestCase(CartTestCase):
    def setUp(self):
        self.sign_in_as(CUSTOMER)
        self.add(42)
        self.add(43)

    def cart(self):
        return self.client.session.get(CART_KEY, {})

    def key_on_page(self, response=None):
        response = response or self.view()
        return response.context["request_key"]

    def reserve(self, answer=(201, ORDER), key=None, changes=None):
        key = key or self.key_on_page(self.view(changes))
        side_effect = answer if isinstance(answer, Exception) else None
        with patch_service("create_reservation", return_value=None if side_effect else answer,
                           side_effect=side_effect) as call, \
                patch_service("listing_offer", side_effect=offers(changes)), \
                patch_service("get_order", return_value=(200, ORDER)):
            response = self.client.post(reverse("cart-checkout"), {"request_key": key}, follow=True)
        return response, call, key


class ReserveButtonTests(CheckoutTestCase):
    """Done when: the customer taps Reserve in the cart..."""

    def test_the_cart_has_a_reserve_button_with_a_checkout_key(self):
        response = self.view()
        self.assertContains(response, 'action="/cart/checkout/"')
        self.assertContains(response, ">Reserve</button>")
        self.assertNotContains(response, "Ordering opens soon")
        key = self.key_on_page(response)
        self.assertEqual(str(uuid.UUID(key)), key)
        self.assertContains(response, f'name="request_key" value="{key}"')

    def test_the_key_stays_while_the_cart_stays_and_changes_with_it(self):
        first = self.key_on_page()
        self.assertEqual(self.key_on_page(), first)
        self.update(43, "increase")
        self.assertNotEqual(self.key_on_page(), first)

    def test_reserve_lands_on_the_order_page_with_the_code_and_total(self):
        # LP-78: was the one-time "Order placed!" message; now the order page.
        response, call, key = self.reserve()
        access, sent_key, lines = call.call_args.args
        self.assertEqual(sent_key, key)
        self.assertEqual(lines, [{"listing_id": 42, "quantity": 1, "expected_unit_price": "4.50"},
                                 {"listing_id": 43, "quantity": 1, "expected_unit_price": "3.00"}])
        self.assertEqual(response.redirect_chain[-1][0], reverse("order-detail", args=[ORDER["reservation_ref"]]))
        for text in ("TM9AY3", "Pay $7.50 in cash at pickup.", "Pick up by <strong>19:15</strong>", "(Thu 19 Nov)",
                     "Cedar Bakery"):
            self.assertContains(response, text)
        self.assertEqual(self.lines(), {})  # the cart is emptied

    def test_reserve_is_post_only(self):
        self.assertEqual(self.client.get(reverse("cart-checkout")).status_code, 405)

    def test_an_old_page_without_a_key_does_nothing(self):
        with patch_service("create_reservation") as call:
            response = self.client.post(reverse("cart-checkout"), {"request_key": "old"}, follow=True)
        call.assert_not_called()
        self.assertContains(response, "press Reserve again")

    def test_reserve_is_disabled_while_something_is_no_longer_available(self):
        response = self.view({42: None})
        self.assertContains(response, "Remove what is no longer available, then reserve.")
        self.assertNotContains(response, 'action="/cart/checkout/"')


class SomethingChangedTests(CheckoutTestCase):
    """Done when: if anything changed, no order is made and the cart shows
    what changed."""

    def test_only_one_left_is_shown_on_its_line_and_the_cart_is_trimmed(self):
        self.add(42)  # 2 croissants
        response, _, key = self.reserve(changed({"listing_id": 42, "title": "Croissant Box", "code": "only_n_left",
                                                 "detail": "Only 1 left.", "free_quantity": 1}))
        self.assertContains(response, "Only 1 left.")
        self.assertContains(response, "nothing was reserved")
        self.assertEqual(self.lines()["42"], 1)
        self.assertNotEqual(self.key_on_page(), key)  # next Reserve is a new attempt
        self.assertNotContains(response, "Order placed!")

    def test_taken_down(self):
        future = {42: {**CROISSANTS, "pickup_end": "2099-01-01T10:00:00Z"}}
        self.view(future)
        response, _, _ = self.reserve(changed({"listing_id": 42, "code": "taken_down",
                                               "detail": "This offer was taken down."}), changes=future)
        self.assertContains(response, "This offer was taken down.")

    def test_pickup_ended_when_the_window_is_over(self):
        # The test offers' window (5 Nov) is over, so "no longer available" says why.
        response, _, _ = self.reserve(changed({"listing_id": 42, "code": "not_available",
                                               "detail": "This offer is no longer available."}))
        self.assertContains(response, "Pickup ended.")

    def test_price_changed_shows_on_the_line_and_the_cart_shows_the_new_price(self):
        dearer = {43: {**BREAD, "discounted_price": "3.50"}}
        response, _, _ = self.reserve(changed({"listing_id": 43, "code": "price_changed",
                                               "detail": "The price changed to $3.50.", "unit_price": "3.50"}),
                                      changes=dearer)
        self.assertContains(response, "The price changed to $3.50.")
        self.assertContains(response, "$3.50 each")
        self.assertEqual(self.cart()["prices"]["43"], "3.50")


class DoubleClickAndOutageTests(CheckoutTestCase):
    """Done when: double click or lost internet still gives one order; if
    Marketplace is down the customer sees "Try again"."""

    def test_double_click_sends_the_same_key_twice(self):
        key = self.key_on_page()
        first, first_call, _ = self.reserve(key=key)
        # The second click comes from the same page, before it reloaded.
        self.add(42)
        second, second_call, _ = self.reserve(answer=(200, ORDER), key=key)
        self.assertEqual(first_call.call_args.args[1], second_call.call_args.args[1])

    def test_reservations_unreachable_says_try_again_and_keeps_the_cart(self):
        key = self.key_on_page()
        response, _, _ = self.reserve(answer=ServiceUnavailable("reservations"), key=key)
        self.assertContains(response, "Nothing was reserved yet. Please press Reserve again.")
        self.assertIn("Reserve again", TRY_AGAIN)
        self.assertEqual(set(self.lines()), {"42", "43"})
        self.assertEqual(self.key_on_page(), key)  # pressing again continues the same order

    def test_marketplace_down_says_try_again_and_keeps_the_key(self):
        key = self.key_on_page()
        response, _, _ = self.reserve(answer=(202, {"code": "try_again",
                                                    "detail": "We couldn't confirm your order yet. Please press Reserve again."}),
                                      key=key)
        self.assertContains(response, "Please press Reserve again.")
        self.assertNotContains(response, "Order placed!")
        self.assertEqual(self.key_on_page(), key)

    def test_nothing_could_be_checked_says_try_again(self):
        response, _, _ = self.reserve(answer=(503, {"code": "try_again", "detail": "Nothing was reserved; please try again."}))
        self.assertContains(response, "please try again")


class WhoCanOrderTests(CheckoutTestCase):
    """Done when: only signed-in customers can order; at most 2 active
    orders; a blocked customer cannot order."""

    def test_signed_out_visitors_are_sent_to_log_in(self):
        self.client = self.client_class()  # a fresh, signed-out browser
        with patch_service("create_reservation") as call:
            response = self.client.post(reverse("cart-checkout"), {"request_key": str(uuid.uuid4())})
        self.assertTrue(response["Location"].startswith(reverse("login")))
        call.assert_not_called()

    def test_restaurant_account_refused(self):
        self.client = self.client_class()  # a fresh, signed-out browser
        self.sign_in_as(OPERATOR)
        with patch_service("create_reservation") as call:
            self.client.post(reverse("cart-checkout"), {"request_key": str(uuid.uuid4())})
        call.assert_not_called()

    def test_a_third_order_is_refused_with_a_clear_message(self):
        detail = "You already have 2 orders waiting for pickup. Pick one up or cancel one before ordering again."
        response, _, _ = self.reserve(answer=(409, {"code": "too_many_orders", "detail": detail}))
        self.assertContains(response, "You already have 2 orders waiting for pickup.")
        self.assertEqual(set(self.lines()), {"42", "43"})

    def test_a_blocked_customer_sees_why(self):
        response, _, _ = self.reserve(answer=(403, {"code": "customer_blocked",
                                                    "detail": "Your account can't place orders right now."}))
        self.assertContains(response, "Your account can&#x27;t place orders right now.")
