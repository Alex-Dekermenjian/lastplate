"""LP-27: Cancel order on the customer's order page.

Reservations (faked here) decides whether the order may be cancelled and puts
the food back on sale; the web page shows the button, the confirm step, and
the answer. After pick-up-by the button is replaced by the shop's phone.
"""

from django.test import Client
from django.urls import reverse

from .services import ServiceUnavailable
from .test_accounts import patch_service
from .test_lp56_login_signup import OPERATOR
from .test_lp78_my_orders import CANCELLED, EXPIRED, PAID, OrderPagesTestCase, make_order


class CancelButtonTests(OrderPagesTestCase):
    def test_reserved_order_has_cancel_with_a_confirm_step(self):
        order = make_order()
        response, _ = self.order_page(order)
        html = response.content.decode()
        self.assertIn('<details class="confirm">', html)
        self.assertIn('class="button button--danger-outline button--block">Cancel order</summary>', html)
        self.assertIn(f'action="{reverse("order-cancel", args=[order["reservation_ref"]])}"', html)
        self.assertIn("Yes, cancel order", html)
        self.assertIn("csrfmiddlewaretoken", html)
        # The phone notice is there for countdown.js, but hidden until pick-up-by.
        self.assertIn("data-after-deadline hidden", html)

    def test_after_pick_up_by_the_button_is_replaced_by_the_shops_phone(self):
        response, _ = self.order_page(make_order(minutes_left=-5))
        html = response.content.decode()
        self.assertNotIn("Cancel order", html)
        self.assertNotIn("data-before-deadline", html)
        self.assertIn("The pick-up time has passed, so this order can't be cancelled here.", html)
        self.assertIn('Please call Cedar Bakery on <a href="tel:+9611234567">+961 1 234 567</a>.', html)
        self.assertNotIn("data-after-deadline hidden", html)

    def test_after_pick_up_by_without_a_saved_phone(self):
        response, _ = self.order_page(make_order(minutes_left=-5, shop_phone=""))
        self.assertContains(response, "Please contact the restaurant.")
        self.assertNotContains(response, "Cancel order")

    def test_paid_cancelled_and_expired_orders_have_no_cancel(self):
        for order in (PAID, CANCELLED, EXPIRED):
            with self.subTest(order["status"]):
                response, _ = self.order_page(order)
                self.assertNotContains(response, "Cancel order")
                self.assertNotContains(response, "data-after-deadline")


class CancelActionTests(OrderPagesTestCase):
    def post_cancel(self, ref, **patch):
        with patch_service("cancel_order", **patch) as call:
            response = self.client.post(reverse("order-cancel", args=[ref]))
        return response, call

    def shown_messages(self, response):
        # Only the messages this request added (redirects are not followed here).
        return [str(m) for m in response.wsgi_request._messages._queued_messages]

    def test_cancel_asks_reservations_and_says_the_food_is_back_on_sale(self):
        order = make_order()
        cancelled = {**order, "status": "CANCELLED", "status_label": "Cancelled", "cancelled_by": "Customer"}
        response, call = self.post_cancel(order["reservation_ref"], return_value=(200, cancelled))
        self.assertRedirects(response, reverse("order-detail", args=[order["reservation_ref"]]),
                             fetch_redirect_response=False)
        self.assertEqual(str(call.call_args.args[1]), order["reservation_ref"])
        self.assertEqual(self.shown_messages(response),
                         ["Your order is cancelled. The food is back on sale for someone else."])

    def test_refusals_from_reservations_are_shown_as_they_are(self):
        order = make_order()
        for code, detail in (("already_cancelled", "This order is already cancelled."),
                             ("already_paid", "This order was already picked up and paid, so it can't be cancelled."),
                             ("expired", "This order has expired, so it can't be cancelled."),
                             ("pickup_time_passed", "The pick-up time has passed, so this order can't be "
                                                    "cancelled here any more. Please call the restaurant on "
                                                    "+961 1 234 567.")):
            with self.subTest(code):
                response, _ = self.post_cancel(order["reservation_ref"],
                                               return_value=(409, {"code": code, "detail": detail}))
                self.assertEqual(response.status_code, 302)
                self.assertEqual(self.shown_messages(response), [detail])

    def test_someone_elses_order(self):
        order = make_order()
        response, _ = self.post_cancel(order["reservation_ref"],
                                       return_value=(404, {"code": "not_found", "detail": "No such order."}))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.shown_messages(response), ["No such order."])

    def test_reservations_down_says_so_without_claiming_success(self):
        order = make_order()
        for patch in ({"side_effect": ServiceUnavailable("down")}, {"return_value": (503, None)}):
            with self.subTest(patch=list(patch)):
                response, _ = self.post_cancel(order["reservation_ref"], **patch)
                self.assertEqual(self.shown_messages(response),
                                 ["We couldn't confirm the cancellation. If your order still shows Reserved, "
                                  "please try again."])

    def test_only_post(self):
        order = make_order()
        with patch_service("cancel_order", side_effect=AssertionError("must not call")):
            response = self.client.get(reverse("order-cancel", args=[order["reservation_ref"]]))
        self.assertEqual(response.status_code, 405)

    def test_only_signed_in_customers(self):
        url = reverse("order-cancel", args=[make_order()["reservation_ref"]])
        with patch_service("cancel_order", side_effect=AssertionError("must not call")):
            signed_out = Client().post(url)
            self.assertEqual(signed_out.status_code, 302)
            self.assertTrue(signed_out["Location"].startswith(reverse("login")))
            self.sign_in_as(OPERATOR)
            self.assertEqual(self.client.post(url).status_code, 302)
