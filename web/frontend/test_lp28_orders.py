"""LP-28: the restaurant's New orders page with Accept and Reject."""

from django.urls import reverse
from django.utils.html import escape

from .services import ServiceUnavailable
from .test_accounts import WebAuthTestCase, patch_service
from .test_lp14_permissions import NoServiceCalls

REF = "6f1c2a8e-1b7d-4e43-9a52-3a0f9d1e2b11"
ORDER = {
    "ref": REF, "customer_name": "Rami", "status": "PLACED", "payment_status": "CASH_DUE", "rejection_reason": "",
    "created_at": "2026-11-05T14:00:00Z", "decided_at": None, "total": "14.00",
    "lines": [
        {"listing_id": 42, "title": "Croissant Box", "unit_price": "4.50", "quantity": 2,
         "pickup_start": "2026-11-05T16:00:00Z", "pickup_end": "2026-11-05T18:00:00Z"},
    ],
}


class OrdersPageTests(WebAuthTestCase):
    url = reverse("shop-orders")
    decide_url = reverse("shop-order-decide", args=[REF])

    def setUp(self):
        self.sign_in_session()

    def test_lists_new_orders_with_accept_and_reject(self):
        with patch_service("shop_orders", return_value=(200, [ORDER])) as api:
            response = self.client.get(self.url)

        api.assert_called_once_with("access-token-1")
        for text in ("Rami", "2 ×", "Croissant Box", "$14.00", "Accept order", "Reject order",
                     "Reason for rejecting", self.decide_url):
            self.assertContains(response, text)

    def test_no_orders_and_service_down(self):
        with patch_service("shop_orders", return_value=(200, [])):
            self.assertContains(self.client.get(self.url), "No new orders right now.")
        with patch_service("shop_orders", side_effect=ServiceUnavailable("reservations")):
            self.assertContains(self.client.get(self.url), "can't load your orders")

    def test_accept(self):
        with patch_service("accept_order", return_value=(200, {**ORDER, "status": "ACCEPTED"})) as api:
            response = self.client.post(self.decide_url, {"decision": "accept"}, follow=False)

        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        api.assert_called_once_with("access-token-1", REF)

    def test_reject_sends_the_reason(self):
        with patch_service("reject_order", return_value=(200, {**ORDER, "status": "REJECTED"})) as api:
            self.client.post(self.decide_url, {"decision": "reject", "reason": "  Sold out early.  "})
        api.assert_called_once_with("access-token-1", REF, "Sold out early.")

    def test_reject_without_a_reason_never_calls_the_service(self):
        with patch_service("reject_order") as api, patch_service("shop_orders", return_value=(200, [ORDER])):
            response = self.client.post(self.decide_url, {"decision": "reject", "reason": " "}, follow=True)
        api.assert_not_called()
        self.assertContains(response, "Give a reason for rejecting")

    def test_a_double_click_shows_the_clear_message(self):
        conflict = (409, {"code": "already_decided", "detail": "This order was already accepted."})
        with patch_service("accept_order", return_value=conflict), \
                patch_service("shop_orders", return_value=(200, [])):
            response = self.client.post(self.decide_url, {"decision": "accept"}, follow=True)
        self.assertContains(response, "This order was already accepted.")

    def test_unknown_outcome_is_explained(self):
        with patch_service("accept_order", side_effect=ServiceUnavailable("reservations")), \
                patch_service("shop_orders", return_value=(200, [])):
            response = self.client.post(self.decide_url, {"decision": "accept"}, follow=True)
        self.assertContains(response, escape("can't tell whether your decision was saved"))


class OrdersPagePermissionTests(WebAuthTestCase):
    def test_signed_out_and_customers_are_kept_out(self):
        url = reverse("shop-orders")
        decide = reverse("shop-order-decide", args=[REF])
        with NoServiceCalls() as calls:
            self.assertEqual(self.client.get(url).status_code, 302)
            self.assertEqual(self.client.post(decide, {"decision": "accept"}).status_code, 302)
        self.assertFalse(any(mock.called for mock in calls.mocks.values()))
