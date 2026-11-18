"""LP-79: the restaurant cancels from Today's orders, with a reason.

Reservations (faked here) decides: this shop's order, still Reserved, before
pick-up-by; the food leaves stock. The web pages show the buttons, the reason
and confirm step, and the answer; the customer's order page shows the reason.
"""

from uuid import UUID

from django.urls import reverse

from .services import ServiceUnavailable
from .test_accounts import APPROVED_SHOP, WebAuthTestCase, patch_service
from .test_lp78_my_orders import OrderPagesTestCase, make_order

REASONS = ("Food ran out", "Closing early", "Other")


class ShopCancelTestCase(WebAuthTestCase):
    def setUp(self):
        self.sign_in_session()
        shop = patch_service("my_shop", return_value=(200, APPROVED_SHOP))
        shop.start()
        self.addCleanup(shop.stop)
        self.order = {**make_order(), "customer_first_name": "Maya"}
        self.body = {"orders": [self.order], "branches": [{"id": 7, "name": "Hamra"}, {"id": 8, "name": "Verdun"}],
                     "count": 1}
        orders = patch_service("shop_orders", return_value=(200, self.body))
        orders.start()
        self.addCleanup(orders.stop)

    def page(self, **params):
        return self.client.get(reverse("today-orders"), params).content.decode()

    def shown_messages(self, response):
        return [str(m) for m in response.wsgi_request._messages._queued_messages]


class TodayOrdersButtonsTests(ShopCancelTestCase):
    def test_each_reserved_order_has_cancel_with_reason_and_confirm(self):
        html = self.page()
        self.assertIn(f'action="{reverse("shop-cancel-order", args=[self.order["reservation_ref"]])}"', html)
        self.assertIn('class="button button--danger-outline">Cancel</summary>', html)
        self.assertIn("Yes, cancel order", html)
        for reason in REASONS:
            self.assertIn(f'name="reason" value="{reason}" required', html)
        self.assertIn("not put back on sale", html)

    def test_cancel_all_for_today_asks_branch_reason_and_confirm(self):
        html = self.page(branch_id="8")
        self.assertIn(f'action="{reverse("shop-cancel-all")}"', html)
        self.assertIn(">Cancel all for today</summary>", html)
        self.assertIn("Yes, cancel all for today", html)
        self.assertIn('<option value="8" selected>Verdun</option>', html)
        self.assertEqual(html.count('name="reason" value="Closing early" required'), 2)  # all + the one order

    def test_no_cancel_after_pick_up_by_or_on_the_paid_tab(self):
        self.order["pick_up_by"] = make_order(minutes_left=-5)["pick_up_by"]
        html = self.page()
        self.assertNotIn("Yes, cancel order", html)
        self.assertIn("Cancel all for today", html)
        self.body["orders"] = [{**make_order("COLLECTED", "Paid"), "customer_first_name": "Maya"}]
        html = self.page(status="COLLECTED")
        self.assertNotIn("Yes, cancel order", html)
        self.assertNotIn("Cancel all for today", html)


class CancelOneTests(ShopCancelTestCase):
    def post(self, reason="Food ran out", **patch):
        ref = self.order["reservation_ref"]
        with patch_service("shop_cancel_order", **patch) as call:
            response = self.client.post(reverse("shop-cancel-order", args=[ref]),
                                        {"reason": reason, "return_branch": "7"})
        return response, call

    def test_sends_the_reason_and_reports_success(self):
        response, call = self.post(return_value=(200, {**self.order, "status": "CANCELLED"}))
        call.assert_called_once_with("access-token-1", UUID(self.order["reservation_ref"]), "Food ran out")
        self.assertRedirects(response, reverse("today-orders") + "?branch_id=7", fetch_redirect_response=False)
        self.assertEqual(self.shown_messages(response), ["Order TM9AY3 is cancelled. The customer can see your reason."])

    def test_refusals_are_shown_as_reservations_says(self):
        for status, detail in ((400, "Choose a reason: Food ran out, Closing early, Other."),
                               (404, "No such order."),
                               (409, "This order was already picked up and paid, so it can't be cancelled.")):
            response, _ = self.post("", return_value=(status, {"code": "x", "detail": detail}))
            self.assertEqual(self.shown_messages(response), [detail])

    def test_reservations_down_does_not_claim_success(self):
        response, _ = self.post(side_effect=ServiceUnavailable("reservations"))
        self.assertIn("couldn't confirm", self.shown_messages(response)[0])

    def test_only_post(self):
        url = reverse("shop-cancel-order", args=[self.order["reservation_ref"]])
        self.assertEqual(self.client.get(url).status_code, 405)


class CancelAllTests(ShopCancelTestCase):
    def post(self, data, **patch):
        with patch_service("shop_cancel_all", **patch) as call:
            response = self.client.post(reverse("shop-cancel-all"), data)
        return response, call

    def test_sends_branch_and_reason_and_reports_the_count(self):
        response, call = self.post({"branch_id": "7", "reason": "Closing early"},
                                   return_value=(200, {"cancelled": 3, "orders": []}))
        call.assert_called_once_with("access-token-1", 7, "Closing early")
        self.assertRedirects(response, reverse("today-orders"), fetch_redirect_response=False)
        self.assertEqual(self.shown_messages(response), ["3 orders cancelled. Customers can see your reason."])

    def test_nothing_left_and_bad_branch(self):
        response, _ = self.post({"branch_id": "7", "reason": "Other"}, return_value=(200, {"cancelled": 0, "orders": []}))
        self.assertEqual(self.shown_messages(response), ["No Reserved orders were left to cancel for today."])
        response, call = self.post({"branch_id": "", "reason": "Other"},
                                   return_value=(404, {"code": "not_found", "detail": "No such branch."}))
        call.assert_called_once_with("access-token-1", None, "Other")
        self.assertEqual(self.shown_messages(response), ["No such branch."])


class CustomerSeesTheReasonTests(OrderPagesTestCase):
    def test_order_page_shows_the_restaurants_reason(self):
        order = make_order("CANCELLED", "Cancelled", cancelled_by="Restaurant", cancel_reason="Food ran out")
        response, _ = self.order_page(order)
        self.assertContains(response, "Cancelled by restaurant")
        self.assertContains(response, "The restaurant cancelled this order: Food ran out")

    def test_no_reason_line_when_the_customer_cancelled(self):
        order = make_order("CANCELLED", "Cancelled", cancelled_by="Customer", cancel_reason="Cancelled by the customer")
        response, _ = self.order_page(order)
        self.assertNotContains(response, "The restaurant cancelled this order")
