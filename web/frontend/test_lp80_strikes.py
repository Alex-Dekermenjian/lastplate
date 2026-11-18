"""LP-80: the strike warning on My orders and the admin's Customers console."""

import uuid

from django.urls import reverse

from .services import ServiceUnavailable
from .test_accounts import patch_service
from .test_lp56_login_signup import CUSTOMER
from .test_lp57_console import ADMIN, ConsoleTestCase
from .test_lp78_my_orders import EXPIRED, OrderPagesTestCase

REF = str(uuid.uuid4())
CUSTOMER_DETAIL = {
    "customer_user_id": 501, "first_name": "Maya", "count": 1, "warning": False, "blocked_until": None,
    "expired_orders": [{"reservation_ref": REF, "pickup_code": "TM9AY3", "pick_up_by": "2026-11-14T17:00:00+00:00",
                        "shop_name": "Cedar Bakery", "branch_name": "", "total_cash": "4.50",
                        "items": [{"title": "Glazed donuts", "quantity": 2}], "counts": True, "removed": None}],
}


class StrikeWarningTests(OrderPagesTestCase):
    def my_orders(self, strikes):
        with patch_service("my_orders", return_value=(200, {"orders": [EXPIRED], "strikes": strikes})):
            return self.client.get(reverse("my-orders"))

    def test_no_warning_below_two_strikes(self):
        response = self.my_orders({"count": 1, "warning": False, "blocked_until": None, "block_at": 3})
        self.assertNotContains(response, "expired without being picked up")

    def test_two_strikes_show_a_warning(self):
        response = self.my_orders({"count": 2, "warning": True, "blocked_until": None, "block_at": 3})
        self.assertContains(response, "2 of your orders expired without being picked up")
        self.assertContains(response, "you won't be able to order for 7 days")

    def test_three_strikes_say_until_when_and_that_browsing_works(self):
        response = self.my_orders({"count": 3, "warning": True, "blocked_until": "2026-11-21T17:00:00+00:00",
                                   "block_at": 3})
        # 17:00 UTC is 19:00 in Beirut.
        self.assertContains(response, "You can't place orders until Sat 21 Nov, 19:00")
        self.assertContains(response, "You can still browse food")

    def test_an_older_reservations_answer_without_strikes_still_works(self):
        with patch_service("my_orders", return_value=(200, {"orders": [EXPIRED]})):
            self.assertEqual(self.client.get(reverse("my-orders")).status_code, 200)


class CustomersConsoleTests(ConsoleTestCase):
    def setUp(self):
        self.sign_in_as(ADMIN)

    def test_only_the_admin_opens_it(self):
        self.sign_in_as(CUSTOMER)
        with patch_service("admin_customers") as api:
            response = self.client.get(reverse("console-customers"))
        self.assertNotEqual(response.status_code, 200)
        api.assert_not_called()

    def test_lists_customers_with_strikes(self):
        customers = [{"customer_user_id": 501, "first_name": "Maya", "count": 3, "warning": True,
                      "blocked_until": "2026-11-21T17:00:00+00:00"}]
        with patch_service("admin_customers", return_value=(200, {"customers": customers})):
            response = self.client.get(reverse("console-customers"))
        self.assertContains(response, "Maya")
        self.assertContains(response, "3 strikes")
        self.assertContains(response, reverse("console-customer", args=[501]))

    def test_service_down_shows_a_message(self):
        with patch_service("admin_customers", side_effect=ServiceUnavailable("reservations")):
            self.assertContains(self.client.get(reverse("console-customers")), "can't load customers")

    def test_shows_expired_history_with_a_remove_form(self):
        with patch_service("admin_customer", return_value=(200, CUSTOMER_DETAIL)):
            response = self.client.get(reverse("console-customer", args=[501]))
        self.assertContains(response, "Cedar Bakery")
        self.assertContains(response, "Remove strike")
        self.assertContains(response, REF)

    def test_removing_a_strike_needs_a_reason(self):
        url = reverse("console-customer", args=[501])
        with patch_service("admin_customer", return_value=(200, CUSTOMER_DETAIL)), \
                patch_service("admin_remove_strike") as remove:
            response = self.client.post(url, {"reservation_ref": REF, "reason": "   "})
        remove.assert_not_called()
        self.assertContains(response, "Say why the strike is removed.")

    def test_removing_a_strike_with_a_reason(self):
        url = reverse("console-customer", args=[501])
        with patch_service("admin_remove_strike", return_value=(200, {})) as remove:
            response = self.client.post(url, {"reservation_ref": REF, "reason": "Shop was closed"})
        self.assertRedirects(response, url, fetch_redirect_response=False)
        self.assertEqual((str(remove.call_args.args[1]), remove.call_args.args[2]), (REF, "Shop was closed"))
