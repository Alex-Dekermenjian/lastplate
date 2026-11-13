"""LP-78: the order page (shown at the counter) and My orders.

Orders come from the Reservations API (faked here); the pickup place was
saved with the order at checkout, so Marketplace is only asked for the names
of orders placed before LP-78.
"""

import uuid
from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from .services import ServiceUnavailable
from .test_accounts import patch_service
from .test_lp56_login_signup import CUSTOMER, OPERATOR
from .test_lp62_cart import CartTestCase

ADMIN = {**CUSTOMER, "id": 99, "email": "admin@example.test", "role": "ADMIN"}


def make_order(status="CONFIRMED", label="Reserved", minutes_left=58, **changes):
    now = timezone.now()
    order = {
        "reservation_ref": str(uuid.uuid4()), "status": status, "status_label": label,
        "pickup_code": "TM9AY3", "pick_up_by": (now + timedelta(minutes=minutes_left, seconds=30)).isoformat(),
        "created_at": now.isoformat(), "total_cash": "4.50", "shop_id": 3, "branch_id": 7,
        "cancelled_by": None, "collected_at": None,
        "shop_name": "Cedar Bakery", "branch_name": "Hamra", "pickup_address": "12 Bliss Street, Beirut",
        "shop_phone": "+961 1 234 567",
        "lines": [{"listing_id": 41, "title": "Glazed donuts", "quantity": 2, "unit_price": "1.50",
                   # 16:00-19:00 UTC on 19 Nov is 18:00-21:00 in Beirut.
                   "pickup_start": "2026-11-19T16:00:00+00:00", "pickup_end": "2026-11-19T19:00:00+00:00"},
                  {"listing_id": 42, "title": "Croissant", "quantity": 1, "unit_price": "1.50",
                   "pickup_start": "2026-11-19T16:00:00+00:00", "pickup_end": "2026-11-19T19:00:00+00:00"}],
    }
    order.update(changes)
    return order


PAID = make_order("COLLECTED", "Paid")
CANCELLED = make_order("CANCELLED", "Cancelled", cancelled_by="Restaurant")
EXPIRED = make_order("EXPIRED", "Expired")


class OrderPagesTestCase(CartTestCase):
    def setUp(self):
        self.sign_in_as(CUSTOMER)

    def order_page(self, order, status=200, **patches):
        with patch_service("get_order", return_value=(status, order if status == 200 else {"code": "not_found"})), \
                patch_service("restaurant_page", **(patches or {"side_effect": AssertionError("not needed")})) as shop:
            response = self.client.get(reverse("order-detail", args=[order["reservation_ref"]]))
        return response, shop

    def my_orders(self, orders, **patches):
        with patch_service("my_orders", return_value=(200, {"orders": orders})), \
                patch_service("restaurant_page", **(patches or {"side_effect": AssertionError("not needed")})):
            return self.client.get(reverse("my-orders"))


class OrderPageTests(OrderPagesTestCase):
    """The page the customer shows at the counter."""

    def test_shows_everything_needed_at_the_counter(self):
        response, _ = self.order_page(make_order())
        for text in ('class="order-code__value"', ">TM9AY3<", "Reserved", "Show this code at the counter",
                     "2 × Glazed donuts", "$1.50 each", "$3.00", "1 × Croissant", "Pay $4.50 in cash at pickup.",
                     "Cedar Bakery · Hamra", "12 Bliss Street, Beirut", 'href="tel:+9611234567"',
                     "Thu 19 Nov, 18:00–21:00", "(Lebanon time)"):
            self.assertContains(response, text, html=False)

    def test_pick_up_by_in_24_hour_time_with_a_live_countdown(self):
        order = make_order(minutes_left=58)
        response, _ = self.order_page(order)
        deadline = timezone.localtime(timezone.datetime.fromisoformat(order["pick_up_by"]),
                                      timezone.get_fixed_timezone(120))
        self.assertContains(response, f"Pick up by <strong>{deadline:%H:%M}</strong>")
        self.assertContains(response, "58 min left")
        self.assertContains(response, f'data-deadline="')
        self.assertContains(response, "js/countdown.js")
        self.assertNotContains(response, " pm")
        self.assertNotContains(response, " am<")

    def test_longer_waits_show_hours(self):
        response, _ = self.order_page(make_order(minutes_left=125))
        self.assertContains(response, "2 h 5 min left")

    def test_pick_up_time_has_passed_while_still_reserved(self):
        response, _ = self.order_page(make_order(minutes_left=-10))
        self.assertContains(response, "Pick-up time has passed")
        self.assertNotContains(response, "data-deadline=")
        self.assertNotContains(response, "min left")

    def test_finished_orders_grey_the_code_and_drop_the_countdown(self):
        for order, label in ((PAID, "Paid"), (CANCELLED, "Cancelled by restaurant"), (EXPIRED, "Expired")):
            response, _ = self.order_page(order)
            self.assertContains(response, "order-code--done")
            self.assertContains(response, label)
            self.assertNotContains(response, "min left")
            self.assertNotContains(response, "Show this code at the counter")
            self.assertNotContains(response, "Pick up by")

    def test_each_cancelled_by_is_named(self):
        for who, label in (("Customer", "Cancelled by customer"), ("Admin", "Cancelled by admin")):
            response, _ = self.order_page(make_order("CANCELLED", "Cancelled", cancelled_by=who))
            self.assertContains(response, label)

    def test_saved_address_is_shown_when_marketplace_is_down(self):
        response, shop = self.order_page(make_order(), side_effect=ServiceUnavailable("marketplace"))
        self.assertEqual(response.status_code, 200)
        for text in ("12 Bliss Street, Beirut", "+961 1 234 567", "Cedar Bakery · Hamra", ">TM9AY3<"):
            self.assertContains(response, text)
        shop.assert_not_called()  # saved with the order: Marketplace is not asked

    def test_old_orders_show_just_the_shop_and_branch_name(self):
        old = make_order(shop_name="", branch_name="", pickup_address="", shop_phone="")
        shop = {"shop": {"id": 3, "name": "Cedar Bakery", "address": "Somewhere new",
                         "public_contact": "+961 3 000 000",
                         "branches": [{"id": 7, "name": "Hamra", "address": "New address"},
                                      {"id": 8, "name": "Verdun", "address": "Elsewhere"}]}}
        response, _ = self.order_page(old, return_value=(200, shop))
        self.assertContains(response, "Cedar Bakery · Hamra")
        for live in ("New address", "Somewhere new", "+961 3 000 000"):
            self.assertNotContains(response, live)  # never a live address or phone

    def test_old_orders_still_open_when_marketplace_is_down(self):
        old = make_order(shop_name="", branch_name="", pickup_address="", shop_phone="")
        response, _ = self.order_page(old, side_effect=ServiceUnavailable("marketplace"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Restaurant")
        self.assertContains(response, ">TM9AY3<")

    def test_someone_elses_or_unknown_order_is_not_found(self):
        response, _ = self.order_page(make_order(), status=404)
        self.assertContains(response, "We can't find this order", status_code=404)
        self.assertNotContains(response, "TM9AY3", status_code=404)

    def test_reservations_down_shows_a_calm_message(self):
        with patch_service("get_order", side_effect=ServiceUnavailable("reservations")):
            response = self.client.get(reverse("order-detail", args=[uuid.uuid4()]))
        self.assertContains(response, "Can&#x27;t load your orders right now, please try again.", status_code=503)


class MyOrdersTests(OrderPagesTestCase):
    def test_active_on_top_then_past(self):
        active = make_order()
        response = self.my_orders([PAID, active, CANCELLED, EXPIRED])
        html = response.content.decode()
        self.assertLess(html.index("<h2>Active</h2>"), html.index("<h2>Past</h2>"))
        self.assertEqual([o["ref"] for o in response.context["active_orders"]], [active["reservation_ref"]])
        self.assertEqual([o["status_label"] for o in response.context["past_orders"]],
                         ["Paid", "Cancelled by restaurant", "Expired"])
        self.assertLess(html.index(active["reservation_ref"]), html.index("<h2>Past</h2>"))

    def test_each_row_shows_shop_items_total_date_status_and_opens_the_order(self):
        order = make_order()
        response = self.my_orders([order])
        today = timezone.localtime(timezone.now(), timezone.get_fixed_timezone(120))
        for text in ("Cedar Bakery · Hamra", "2 × Glazed donuts, 1 × Croissant", "$4.50",
                     f"{today:%a} {today.day} {today:%b}", "Reserved", "58 min left",
                     f'href="{reverse("order-detail", args=[order["reservation_ref"]])}"'):
            self.assertContains(response, text, html=False)

    def test_empty_state(self):
        response = self.my_orders([])
        self.assertContains(response, "No orders yet.")
        self.assertContains(response, f'href="{reverse("browse-offers")}"')
        self.assertContains(response, "Browse food</a>")

    def test_reservations_down_shows_a_calm_message(self):
        for answer in ({"side_effect": ServiceUnavailable("reservations")}, {"return_value": (502, {})}):
            with patch_service("my_orders", **answer):
                response = self.client.get(reverse("my-orders"))
            self.assertContains(response, "Can&#x27;t load your orders right now, please try again.", status_code=503)

    def test_old_orders_look_up_each_restaurant_once(self):
        old = [make_order(shop_name="", pickup_address="") for _ in range(3)]
        shop = {"shop": {"id": 3, "name": "Cedar Bakery", "branches": [{"id": 7, "name": "Hamra"}]}}
        with patch_service("my_orders", return_value=(200, {"orders": old})), \
                patch_service("restaurant_page", return_value=(200, shop)) as lookup:
            response = self.client.get(reverse("my-orders"))
        self.assertContains(response, "Cedar Bakery")
        lookup.assert_called_once_with(3)


class WhoCanSeeTests(OrderPagesTestCase):
    def test_visitors_are_sent_to_log_in(self):
        self.client = self.client_class()
        for url in (reverse("my-orders"), reverse("order-detail", args=[uuid.uuid4()])):
            with patch_service("my_orders") as calls, patch_service("get_order") as one:
                response = self.client.get(url)
            self.assertTrue(response["Location"].startswith(reverse("login")), url)
            calls.assert_not_called()
            one.assert_not_called()

    def test_shop_owners_and_admins_are_blocked(self):
        for account in (OPERATOR, ADMIN):
            self.client = self.client_class()
            self.sign_in_as(account)
            for url in (reverse("my-orders"), reverse("order-detail", args=[uuid.uuid4()])):
                with patch_service("my_orders") as calls, patch_service("get_order") as one:
                    response = self.client.get(url)
                self.assertEqual(response.status_code, 302, url)
                self.assertFalse(response["Location"].startswith(reverse("my-orders")))
                calls.assert_not_called()
                one.assert_not_called()


class MenuLinkTests(OrderPagesTestCase):
    """A plain "My orders" link next to "Browse food", for customers only."""

    LINK = f'<a class="site-nav__link" href="/orders/">My orders</a>'

    def test_customers_see_it_on_every_page(self):
        with patch_service("browse_listings", return_value=(200, [])):
            for url in (reverse("home"), reverse("browse-offers")):
                self.assertContains(self.client.get(url), self.LINK, html=False)
        self.assertContains(self.my_orders([]), self.LINK, html=False)
        self.assertNotContains(self.my_orders([]), 'class="button button--secondary" href="/orders/"')

    def test_others_do_not(self):
        for account in (None, OPERATOR, ADMIN):
            self.client = self.client_class()
            if account:
                self.sign_in_as(account)
            with patch_service("browse_listings", return_value=(200, [])):
                self.assertNotContains(self.client.get(reverse("browse-offers")), "My orders")
