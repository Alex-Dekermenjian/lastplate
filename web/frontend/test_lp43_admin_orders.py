"""LP-43: the admin's Orders, Revenue and Needs attention pages in /console."""

import uuid

from django.urls import reverse

from .services import ServiceUnavailable
from .test_accounts import patch_service
from .test_lp56_login_signup import CUSTOMER, OPERATOR
from .test_lp57_console import ADMIN, ConsoleTestCase

REF = str(uuid.uuid4())
ORDER = {
    "reservation_ref": REF, "pickup_code": "K7M2QX", "status": "COLLECTED", "status_label": "Paid",
    "sync_state": "SYNCED", "needs_attention": False, "created_at": "2026-11-18T08:00:00+00:00",
    "pick_up_by": "2026-11-18T09:00:00+00:00", "collected_at": "2026-11-18T08:30:00+00:00",
    "shop_id": 3, "shop_name": "Cedar Bakery", "branch_name": "Hamra", "customer_user_id": 501,
    "customer_first_name": "Maya", "total_cash": "4.50", "items": [{"title": "Glazed donuts", "quantity": 2}],
    "cancelled_by": "", "cancel_reason": "",
}
ORDERS = {
    "orders": [ORDER], "count": 1, "page": 1, "pages": 1,
    "shops": [{"shop_id": 3, "shop_name": "Cedar Bakery"}, {"shop_id": 9, "shop_name": "Zaatar House"}],
    "statuses": [{"status": "CONFIRMED", "label": "Reserved"}, {"status": "COLLECTED", "label": "Paid"},
                 {"status": "CANCELLED", "label": "Cancelled"}, {"status": "EXPIRED", "label": "Expired"}],
    "needs_attention": 0,
}
PERIOD = {"revenue": "4.50", "paid_orders": 1, "items_saved": 2}
REVENUE = {
    "today": PERIOD, "week": {"revenue": "12.00", "paid_orders": 3, "items_saved": 6},
    "all_time": {"revenue": "40.00", "paid_orders": 9, "items_saved": 20}, "week_start": "2026-11-16",
    "orders": 15, "expired_orders": 2, "shop_cancellations": 1, "needs_attention": 1,
    "shops": [
        {"shop_id": 9, "shop_name": "Zaatar House", "revenue": "30.00", "orders": 8, "paid_orders": 6,
         "items_saved": 14, "expired_orders": 1, "shop_cancellations": 0},
        {"shop_id": 3, "shop_name": "Cedar Bakery", "revenue": "10.00", "orders": 7, "paid_orders": 3,
         "items_saved": 6, "expired_orders": 1, "shop_cancellations": 1},
    ],
}
STUCK = {**ORDER, "status": "EXPIRED", "status_label": "Expired", "sync_state": "NEEDS_ATTENTION",
         "needs_attention": True, "step": "release", "can_retry": True,
         "problem": "Marketplace hasn't confirmed this order's food was put back."}


class OnlyTheAdminTests(ConsoleTestCase):
    """Tests to write: only staff can open these pages."""

    def test_customers_and_restaurants_never_reach_the_api(self):
        pages = [("get", reverse("console-orders"), "admin_orders"),
                 ("get", reverse("console-revenue"), "admin_revenue"),
                 ("get", reverse("console-attention"), "admin_attention"),
                 ("post", reverse("console-retry-order", args=[REF]), "admin_retry_order")]
        for account in (CUSTOMER, OPERATOR):
            self.sign_in_as(account)
            for method, url, api_name in pages:
                with self.subTest(role=account["role"], url=url), patch_service(api_name) as api:
                    response = getattr(self.client, method)(url)
                    self.assertNotEqual(response.status_code, 200)
                    api.assert_not_called()

    def test_signed_out_visitors_are_sent_to_login(self):
        with patch_service("admin_revenue") as api:
            response = self.client.get(reverse("console-revenue"))
        self.assertIn(reverse("login"), response.url)
        api.assert_not_called()


class OrdersPageTests(ConsoleTestCase):
    """Done when: the admin sees every order and can filter by status,
    restaurant or date, and search by code."""

    def setUp(self):
        self.sign_in_as(ADMIN)

    def test_lists_orders_with_their_details(self):
        with patch_service("admin_orders", return_value=(200, ORDERS)):
            response = self.client.get(reverse("console-orders"))
        for text in ("K7M2QX", "Cedar Bakery", "Paid", "2 × Glazed donuts", "Maya", "$4.50",
                     "Placed Wed 18 Nov, 10:00", "Zaatar House"):
            self.assertContains(response, text)

    def test_filters_and_code_are_sent_to_reservations(self):
        with patch_service("admin_orders", return_value=(200, ORDERS)) as api:
            response = self.client.get(reverse("console-orders"), {
                "status": "collected", "shop_id": "3", "date": "2026-11-18", "code": "k7m2qx"})
        filters = api.call_args.args[1]
        self.assertEqual(filters, {"status": "COLLECTED", "shop_id": "3", "date": "2026-11-18", "code": "k7m2qx"})
        self.assertContains(response, '<option value="3" selected>Cedar Bakery</option>', html=True)
        self.assertContains(response, "Clear filters")

    def test_bad_filters_are_dropped(self):
        with patch_service("admin_orders", return_value=(200, ORDERS)) as api:
            self.client.get(reverse("console-orders"), {"status": "PROCESSING", "shop_id": "x",
                                                         "date": "18/11", "code": "<script>", "page": "-1"})
        self.assertEqual(api.call_args.args[1], {})

    def test_pages_keep_the_filters(self):
        with patch_service("admin_orders", return_value=(200, {**ORDERS, "count": 120, "pages": 3, "page": 2})):
            response = self.client.get(reverse("console-orders"), {"status": "EXPIRED", "page": "2"})
        self.assertContains(response, "Page 2 of 3")
        self.assertContains(response, "?status=EXPIRED&amp;page=1")
        self.assertContains(response, "?status=EXPIRED&amp;page=3")

    def test_nothing_found_and_service_down(self):
        with patch_service("admin_orders", return_value=(200, {**ORDERS, "orders": [], "count": 0})):
            self.assertContains(self.client.get(reverse("console-orders"), {"code": "ZZZZZZ"}),
                                "No orders match these filters.")
        with patch_service("admin_orders", side_effect=ServiceUnavailable("reservations")):
            self.assertContains(self.client.get(reverse("console-orders")), "can't load orders")

    def test_points_to_orders_that_need_attention(self):
        with patch_service("admin_orders", return_value=(200, {**ORDERS, "needs_attention": 2})):
            response = self.client.get(reverse("console-orders"))
        self.assertContains(response, "2 orders need attention.")
        self.assertContains(response, reverse("console-attention"))


class RevenuePageTests(ConsoleTestCase):
    """Done when: a dashboard shows total revenue from Paid orders (today, this
    week, all time), number of orders, items saved and expired orders, and the
    same numbers per restaurant, sorted from highest revenue."""

    def setUp(self):
        self.sign_in_as(ADMIN)

    def test_shows_the_marketplace_numbers(self):
        with patch_service("admin_revenue", return_value=(200, REVENUE)):
            response = self.client.get(reverse("console-revenue"))
        for text in ("$4.50", "Today · 1 order", "$12.00", "since Mon 16 Nov", "$40.00", "All time · 9 orders",
                     "Orders placed", "Items saved (Paid)", "Expired orders", "cash from Paid orders only"):
            self.assertContains(response, text)

    def test_restaurants_in_the_order_reservations_sorted_them(self):
        with patch_service("admin_revenue", return_value=(200, REVENUE)):
            page = self.client.get(reverse("console-revenue")).content.decode()
        self.assertLess(page.index("Zaatar House"), page.index("Cedar Bakery"))
        self.assertIn("1 cancelled by the restaurant", page)
        self.assertIn(f"{reverse('console-orders')}?shop_id=9", page)

    def test_service_down(self):
        with patch_service("admin_revenue", side_effect=ServiceUnavailable("reservations")):
            self.assertContains(self.client.get(reverse("console-revenue")), "can't load the numbers")


class NeedsAttentionPageTests(ConsoleTestCase):
    """Done when: a "Needs attention" list shows orders where the two
    services disagree, with a Retry button."""

    def setUp(self):
        self.sign_in_as(ADMIN)

    def test_lists_orders_with_a_retry_button(self):
        with patch_service("admin_attention", return_value=(200, {"orders": [STUCK]})):
            response = self.client.get(reverse("console-attention"))
        self.assertContains(response, "food was put back")
        self.assertContains(response, reverse("console-retry-order", args=[REF]))
        self.assertContains(response, ">Retry</button>")

    def test_no_retry_button_when_it_cannot_be_retried(self):
        with patch_service("admin_attention", return_value=(200, {"orders": [{**STUCK, "can_retry": False}]})):
            response = self.client.get(reverse("console-attention"))
        self.assertNotContains(response, ">Retry</button>")
        self.assertContains(response, "Ask the developers")

    def test_empty_list(self):
        with patch_service("admin_attention", return_value=(200, {"orders": []})):
            self.assertContains(self.client.get(reverse("console-attention")), "Nothing needs attention.")

    def test_retry_says_what_happened(self):
        for result, text in (("fixed", "Fixed: Marketplace confirmed it."),
                             ("still_failing", "still gives an unexpected answer"),
                             ("waiting", "keeps trying every minute")):
            with self.subTest(result=result), \
                    patch_service("admin_retry_order", return_value=(200, {**STUCK, "result": result})) as api, \
                    patch_service("admin_attention", return_value=(200, {"orders": []})):
                response = self.client.post(reverse("console-retry-order", args=[REF]), follow=True)
                self.assertEqual(str(api.call_args.args[1]), REF)
                self.assertContains(response, text)

    def test_retry_refused_and_service_down(self):
        refused = (409, {"code": "not_needed", "detail": "This order doesn't need attention any more."})
        with patch_service("admin_retry_order", return_value=refused), \
                patch_service("admin_attention", return_value=(200, {"orders": []})):
            response = self.client.post(reverse("console-retry-order", args=[REF]), follow=True)
        self.assertContains(response, "doesn&#x27;t need attention any more")
        with patch_service("admin_retry_order", side_effect=ServiceUnavailable("reservations")), \
                patch_service("admin_attention", return_value=(200, {"orders": []})):
            response = self.client.post(reverse("console-retry-order", args=[REF]), follow=True)
        self.assertContains(response, "can&#x27;t tell whether Retry ran")

    def test_retry_needs_post(self):
        with patch_service("admin_retry_order") as api:
            self.assertEqual(self.client.get(reverse("console-retry-order", args=[REF])).status_code, 405)
        api.assert_not_called()


class ConsoleBarTests(ConsoleTestCase):
    def test_console_bar_links_to_orders(self):
        self.sign_in_as(ADMIN)
        with patch_service("admin_customers", return_value=(200, {"customers": []})):
            self.assertContains(self.client.get(reverse("console-customers")), reverse("console-orders"))
