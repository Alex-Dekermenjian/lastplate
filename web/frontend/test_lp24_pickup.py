from uuid import UUID

from django.test import Client
from django.urls import reverse

from .services import ServiceUnavailable
from .test_accounts import APPROVED_SHOP, WebAuthTestCase, patch_service
from .test_lp78_my_orders import make_order
from .test_lp19_checkout import CheckoutTestCase as CartCheckoutTestCase, ORDER


def summary(paid=False, **changes):
    return {**make_order("COLLECTED" if paid else "CONFIRMED", "Paid" if paid else "Reserved"),
            "collection_version": 1 if paid else 0, "can_undo": paid,
            "customer_first_name": "Maya", "customer_phone": "" if paid else "+961 70 123456", **changes}


class PickupPageTests(WebAuthTestCase):
    def setUp(self):
        self.sign_in_session()
        shop = patch_service("my_shop", return_value=(200, APPROVED_SHOP))
        shop.start()
        self.addCleanup(shop.stop)
        self.order = summary()
        self.branches = [{"id": 7, "name": "Hamra"}, {"id": 8, "name": "Verdun"}]
        for name, value in (("shop_orders", {"orders": [], "count": 1, "branches": self.branches}),
                            ("pickup_lookup", self.order), ("pickup_payment", summary(True))):
            patcher = patch_service(name, return_value=(200, value))
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)

    def lookup(self):
        return self.client.post(reverse("pickup-code"), {"branch_id": "7", "pickup_code": "TM9AY3"})

    def pay(self, undo=False, **fields):
        return self.client.post(reverse("pickup-undo" if undo else "pickup-paid", args=[self.order["reservation_ref"]]),
                                {"branch_id": "7", "version": "1" if undo else "0", "cash_received": "yes", **fields})

    def test_form_and_summary_show_items_phone_cash_before_paid(self):
        response = self.client.get(reverse("pickup-code"))
        self.assertContains(response, 'class="field"')
        self.assertContains(response, "Hamra")
        response = self.lookup()
        for text in ("Maya", "2 × Glazed donuts", "$4.50", "+961 70 123456", "Cash received", ">Paid</button>"):
            self.assertContains(response, text)
        self.pickup_lookup.assert_called_once_with("access-token-1", {"branch_id": 7, "pickup_code": "TM9AY3"})
        self.pickup_payment.assert_not_called()
        self.assert_no_secrets(response)

    def test_paid_confirmation_hides_phone_and_offers_undo(self):
        response = self.pay()
        self.assertContains(response, "Paid")
        self.assertContains(response, "Undo Paid")
        self.assertNotContains(response, "+961 70 123456")
        self.pickup_payment.assert_called_once_with("access-token-1", UUID(self.order["reservation_ref"]), {"branch_id": 7, "version": 0, "cash_received": True}, False)

    def test_undo_shows_reserved_and_phone_again(self):
        self.pickup_payment.return_value = (200, summary(collection_version=2))
        response = self.pay(undo=True)
        self.assertContains(response, "Reserved")
        self.assertContains(response, "+961 70 123456")
        self.assertEqual(self.pickup_payment.call_args.args[-1], True)

    def test_wrong_code_and_expired_deadline_are_displayed(self):
        self.pickup_lookup.return_value = (404, {"detail": "No order with this code here"})
        self.assertContains(self.lookup(), "No order with this code here", status_code=404)
        self.pickup_lookup.return_value = (409, {"detail": "The pickup deadline has passed.", "pick_up_by": "2026-11-19T16:00:00Z"})
        response = self.lookup()
        self.assertContains(response, "Pickup deadline", status_code=409)
        self.assertContains(response, "18:00", status_code=409)

    def test_ambiguous_payment_preserves_version_for_safe_retry(self):
        self.pickup_payment.side_effect = ServiceUnavailable("reservations")
        response = self.pay()
        self.assertContains(response, "Retry the same request", status_code=503)
        self.assertContains(response, 'name="version" value="0"', status_code=503)
        self.assertContains(response, 'name="cash_received" value="yes"', status_code=503)
        self.pickup_lookup.assert_not_called()

    def test_roles_and_post_only_mutations(self):
        self.assertEqual(self.client.get(reverse("pickup-paid", args=[self.order["reservation_ref"]])).status_code, 405)
        for role in ("CUSTOMER", "ADMIN"):
            session = self.client.session
            session["account"] = {"id": 7, "role": role}
            session.save()
            self.assertEqual(self.lookup().status_code, 302)
            self.assertEqual(self.pay().status_code, 302)
        self.pickup_lookup.assert_not_called()
        self.pickup_payment.assert_not_called()

    def test_mutations_require_csrf(self):
        secure = Client(enforce_csrf_checks=True)
        secure.cookies = self.client.cookies
        response = secure.post(reverse("pickup-paid", args=[self.order["reservation_ref"]]), {})
        self.assertEqual(response.status_code, 403)
        self.pickup_payment.assert_not_called()

    def test_owner_paid_tab_and_customer_my_orders_show_paid(self):
        paid = summary(True)
        self.shop_orders.return_value = (200, {"orders": [paid], "branches": self.branches, "count": 0})
        response = self.client.get(reverse("today-orders"), {"status": "COLLECTED"})
        self.assertContains(response, "Cash received:")
        self.assertContains(response, "Undo Paid")
        self.assertNotContains(response, "+961 70 123456")
        session = self.client.session
        session["account"] = {"id": 501, "role": "CUSTOMER"}
        session.save()
        with patch_service("my_orders", return_value=(200, {"orders": [paid]})):
            response = self.client.get(reverse("my-orders"))
        self.assertContains(response, "Paid")

    def test_phone_is_hidden_for_paid_even_if_bad_upstream_sends_it(self):
        self.pickup_payment.return_value = (200, summary(True, customer_phone="+961 70 123456"))
        self.assertNotContains(self.pay(), "+961 70 123456")

    def test_consumption_started_or_window_closed_removes_undo_button(self):
        self.pickup_payment.return_value = (200, summary(True, can_undo=False))
        self.assertNotContains(self.pay(), "Undo Paid")

    def test_pending_or_rejected_operator_gets_existing_approval_navigation(self):
        for status in ("PENDING", "REJECTED"):
            with self.subTest(status=status), patch_service("my_shop", return_value=(200, {
                    **APPROVED_SHOP, "approval_status": status, "application_sent": True})):
                self.assertRedirects(self.client.get(reverse("today-orders")), reverse("request-status"), fetch_redirect_response=False)
                self.assertRedirects(self.client.get(reverse("pickup-code")), reverse("request-status"), fetch_redirect_response=False)
        self.shop_orders.assert_not_called()
        self.pickup_lookup.assert_not_called()

    def test_incomplete_or_missing_shop_goes_to_application(self):
        for answer in ((404, {}), (200, {**APPROVED_SHOP, "approval_status": "PENDING", "application_sent": False})):
            with patch_service("my_shop", return_value=answer):
                self.assertRedirects(self.client.get(reverse("today-orders")), reverse("restaurant-apply"), fetch_redirect_response=False)
        self.shop_orders.assert_not_called()

    def test_malformed_payment_response_keeps_original_retry_and_does_not_crash(self):
        for changes in ({"lines": [None]}, {"reservation_ref": "bad"}, {"collection_version": None}):
            with self.subTest(changes=changes):
                self.pickup_payment.return_value = (200, summary(True, **changes))
                response = self.pay()
                self.assertContains(response, "Retry the same request", status_code=503)
                self.assertContains(response, 'name="version" value="0"', status_code=503)
        self.pickup_lookup.assert_not_called()

    def test_malformed_branch_service_response_shows_retry_state(self):
        self.shop_orders.return_value = (200, {"orders": [], "branches": [None], "count": 0})
        self.assertContains(self.client.get(reverse("pickup-code")), "Please try again", status_code=503)


class OptionalPhoneCartTests(CartCheckoutTestCase):
    def test_optional_contact_input_and_forwarding(self):
        page = self.view()
        self.assertContains(page, 'name="customer_phone"')
        with patch_service("create_reservation", return_value=(201, ORDER)) as call:
            response = self.client.post(reverse("cart-checkout"),
                                        {"request_key": self.key_on_page(page), "customer_phone": "+961 70 123456"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(call.call_args.args[-1], "+961 70 123456")
