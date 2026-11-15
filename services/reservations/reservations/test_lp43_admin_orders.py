"""LP-43: the admin's view of every order, the revenue and orders that need
attention. One test class per "Done when" line, plus the card's "Tests to
write": only the admin can open these, and revenue counts only Paid orders."""

import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from unittest import mock
from zoneinfo import ZoneInfo

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from . import admin_orders
from . import marketplace_client as market
from .models import Actor, CancelledBy, Order, Status, SyncState
from .orders import change_status
from .test_lp19_checkout import PUBLIC_PEM, token
from .test_lp78_my_orders import make_order

BEIRUT = ZoneInfo("Asia/Beirut")
ORDERS, REVENUE, ATTENTION = "/api/admin/orders", "/api/admin/revenue", "/api/admin/attention"


def at_shop(order, shop_id, name):
    Order.objects.filter(pk=order.pk).update(shop_id=shop_id, shop_name=name)
    order.refresh_from_db()
    return order


def paid(shop_id=3, name="Cedar Bakery", when=None):
    order = at_shop(make_order(status=Status.COLLECTED, actor=Actor.SHOP), shop_id, name)
    if when is not None:
        Order.objects.filter(pk=order.pk).update(collected_at=when)
    return order


def retry_url(order):
    return f"/api/admin/orders/{order.reservation_ref}/retry"


@override_settings(JWT_PUBLIC_KEY=PUBLIC_PEM)
class AdminApiTestCase(TestCase):
    def setUp(self):
        self.admin = APIClient()
        self.admin.credentials(HTTP_AUTHORIZATION="Bearer " + token(user_id=1, role="ADMIN"))

    def get(self, url, **params):
        response = self.admin.get(url, params)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()


class OnlyTheAdminTests(AdminApiTestCase):
    """Tests to write: only staff can open these pages."""

    def test_customers_and_restaurants_are_refused_and_no_token_is_401(self):
        order = make_order()
        Order.objects.filter(pk=order.pk).update(sync_state=SyncState.NEEDS_ATTENTION)
        requests = [("get", ORDERS), ("get", REVENUE), ("get", ATTENTION), ("post", retry_url(order))]
        for role in ("CUSTOMER", "SHOP_OPERATOR"):
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION="Bearer " + token(user_id=5, role=role))
            for method, url in requests:
                with self.subTest(role=role, url=url):
                    self.assertEqual(getattr(client, method)(url).status_code, 403)
        for method, url in requests:
            with self.subTest(url=url):
                self.assertEqual(getattr(APIClient(), method)(url).status_code, 401)
        # Nothing was retried by the refused calls.
        order.refresh_from_db()
        self.assertEqual(order.sync_state, SyncState.NEEDS_ATTENTION)

    def test_the_admin_can_open_every_page(self):
        for url in (ORDERS, REVENUE, ATTENTION):
            self.get(url)


class EveryOrderWithFiltersTests(AdminApiTestCase):
    """Done when: the admin sees every order and can filter by status,
    restaurant or date, and search by code."""

    def refs(self, **params):
        return {row["reservation_ref"] for row in self.get(ORDERS, **params)["orders"]}

    def test_every_customers_orders_newest_first_without_internal_ones(self):
        first = make_order(customer=11)
        second = make_order(customer=12, status=Status.CANCELLED, actor=Actor.CUSTOMER)
        make_order(customer=13, status=Status.PROCESSING)
        make_order(customer=14, status=Status.NOT_RESERVED)
        data = self.get(ORDERS)
        self.assertEqual([row["reservation_ref"] for row in data["orders"]],
                         [str(second.reservation_ref), str(first.reservation_ref)])
        row = data["orders"][0]
        self.assertEqual((row["status_label"], row["customer_user_id"], row["total_cash"]), ("Cancelled", 12, "3.00"))
        self.assertEqual(row["items"], [{"title": "Glazed donuts", "quantity": 2}])

    def test_filter_by_status(self):
        reserved = make_order()
        done = paid()
        self.assertEqual(self.refs(status="COLLECTED"), {str(done.reservation_ref)})
        self.assertEqual(self.refs(status="confirmed"), {str(reserved.reservation_ref)})

    def test_filter_by_restaurant_lists_restaurants_to_choose(self):
        cedar = at_shop(make_order(), 3, "Cedar Bakery")
        at_shop(make_order(), 9, "Zaatar House")
        at_shop(make_order(customer=77), 9, "Zaatar House")  # listed once, however many orders
        data = self.get(ORDERS, shop_id=3)
        self.assertEqual({row["reservation_ref"] for row in data["orders"]}, {str(cedar.reservation_ref)})
        self.assertEqual(data["shops"], [{"shop_id": 3, "shop_name": "Cedar Bakery"},
                                         {"shop_id": 9, "shop_name": "Zaatar House"}])

    def test_filter_by_day_in_lebanon_time(self):
        late = make_order()
        early = make_order()
        # 23:30 on the 17th and 00:30 on the 18th, Lebanon time.
        Order.objects.filter(pk=late.pk).update(created_at=datetime(2026, 11, 17, 23, 30, tzinfo=BEIRUT))
        Order.objects.filter(pk=early.pk).update(created_at=datetime(2026, 11, 18, 0, 30, tzinfo=BEIRUT))
        self.assertEqual(self.refs(date="2026-11-18"), {str(early.reservation_ref)})
        self.assertEqual(self.refs(date="2026-11-17"), {str(late.reservation_ref)})

    def test_search_by_code(self):
        wanted = make_order()
        make_order(customer=99)
        self.assertEqual(self.refs(code=wanted.pickup_code.lower()), {str(wanted.reservation_ref)})
        self.assertEqual(self.refs(code=f" {wanted.pickup_code[:3]} {wanted.pickup_code[3:]} "),
                         {str(wanted.reservation_ref)})

    def test_bad_filters_are_refused(self):
        for params in ({"status": "PROCESSING"}, {"shop_id": "abc"}, {"date": "18/11/2026"}, {"page": "0"}):
            with self.subTest(params=params):
                response = self.admin.get(ORDERS, params)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["code"], "bad_filter")

    def test_fifty_per_page(self):
        for number in range(admin_orders.PAGE_SIZE + 1):
            make_order(customer=1000 + number)
        first, second = self.get(ORDERS), self.get(ORDERS, page=2)
        self.assertEqual((first["count"], first["pages"], len(first["orders"]), len(second["orders"])),
                         (51, 2, 50, 1))


class RevenueCountsOnlyPaidTests(AdminApiTestCase):
    """Tests to write: revenue counts only Paid orders. Done when: total
    revenue from Paid orders (today, this week, all time), number of orders,
    items saved and expired orders."""

    def test_only_paid_orders_count(self):
        make_order()
        make_order(status=Status.CANCELLED, actor=Actor.CUSTOMER)
        make_order(status=Status.EXPIRED, actor=Actor.SYSTEM)
        make_order(status=Status.PROCESSING)
        paid()
        data = self.get(REVENUE)
        self.assertEqual(data["all_time"], {"revenue": "3.00", "paid_orders": 1, "items_saved": 2})
        self.assertEqual((data["orders"], data["expired_orders"]), (4, 1))

    def test_paid_then_undone_is_not_revenue(self):
        order = paid()
        Order.objects.filter(pk=order.pk).update(sync_state=SyncState.CONSUME_PENDING)  # within the undo window
        change_status(order, Status.CONFIRMED, actor=Actor.SHOP, undo_collection=True)
        self.assertEqual(self.get(REVENUE)["all_time"]["revenue"], "0.00")

    def test_today_this_week_and_all_time_in_lebanon_time(self):
        now = datetime(2026, 11, 18, 12, 0, tzinfo=BEIRUT)  # a Wednesday
        paid(when=datetime(2026, 11, 18, 0, 5, tzinfo=BEIRUT))   # today
        paid(when=datetime(2026, 11, 16, 9, 0, tzinfo=BEIRUT))   # Monday: this week
        paid(when=datetime(2026, 11, 15, 23, 50, tzinfo=BEIRUT))  # last Sunday
        data = admin_orders.revenue(now=now)
        self.assertEqual([data[key]["paid_orders"] for key in ("today", "week", "all_time")], [1, 2, 3])
        self.assertEqual(data["week"]["revenue"], "6.00")
        self.assertEqual(data["week_start"], "2026-11-16")


class PerRestaurantTests(AdminApiTestCase):
    """Done when: the same numbers per restaurant, sorted from highest
    revenue, with shop cancellation counts."""

    def test_sorted_by_revenue_with_cancellations(self):
        paid(3, "Cedar Bakery")
        for _ in range(2):
            paid(9, "Zaatar House")
        at_shop(make_order(status=Status.EXPIRED, actor=Actor.SYSTEM), 3, "Cedar Bakery")
        cancelled = at_shop(make_order(), 3, "Cedar Bakery")
        change_status(cancelled, Status.CANCELLED, actor=Actor.SHOP, reason="Ran out")
        at_shop(make_order(), 12, "")  # never paid, no saved name

        shops = self.get(REVENUE)["shops"]
        self.assertEqual([shop["shop_name"] for shop in shops], ["Zaatar House", "Cedar Bakery", "Restaurant #12"])
        self.assertEqual(shops[0], {"shop_id": 9, "shop_name": "Zaatar House", "revenue": "6.00", "orders": 2,
                                    "paid_orders": 2, "items_saved": 4, "expired_orders": 0, "shop_cancellations": 0})
        cedar = shops[1]
        self.assertEqual((cedar["revenue"], cedar["orders"], cedar["expired_orders"], cedar["shop_cancellations"]),
                         ("3.00", 3, 1, 1))
        self.assertEqual(self.get(REVENUE)["shop_cancellations"], 1)


class NeedsAttentionTests(AdminApiTestCase):
    """Done when: a "Needs attention" list shows orders where the two
    services disagree, with a Retry button."""

    def stuck(self, order):
        Order.objects.filter(pk=order.pk).update(sync_state=SyncState.NEEDS_ATTENTION)
        order.refresh_from_db()
        return order

    def retry(self, order, answer):
        with mock.patch.object(market, "stock_call", return_value=answer) as call:
            response = self.admin.post(retry_url(order))
        return response, call

    def test_lists_only_orders_that_need_attention(self):
        expired = self.stuck(make_order(status=Status.EXPIRED, actor=Actor.SYSTEM))
        make_order(status=Status.EXPIRED, actor=Actor.SYSTEM)
        rows = self.get(ATTENTION)["orders"]
        self.assertEqual([row["reservation_ref"] for row in rows], [str(expired.reservation_ref)])
        self.assertTrue(rows[0]["can_retry"])
        self.assertIn("put back", rows[0]["problem"])
        self.assertEqual(self.get(REVENUE)["needs_attention"], 1)

    def test_retry_release_fixed(self):
        order = self.stuck(make_order(status=Status.EXPIRED, actor=Actor.SYSTEM))
        response, call = self.retry(order, market.DONE)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual((response.json()["result"], response.json()["step"]), ("fixed", "release"))
        call.assert_called_once_with("release", mock.ANY, 41)
        order.refresh_from_db()
        self.assertEqual((order.status, order.sync_state), (Status.EXPIRED, SyncState.SYNCED))
        self.assertEqual(self.get(ATTENTION)["orders"], [])

    def test_retry_still_failing_stays_on_the_list(self):
        order = self.stuck(make_order(status=Status.CANCELLED, actor=Actor.CUSTOMER))
        response, _ = self.retry(order, market.FAULT)
        self.assertEqual(response.json()["result"], "still_failing")
        self.assertEqual(len(self.get(ATTENTION)["orders"]), 1)

    def test_retry_without_an_answer_waits_for_the_timer(self):
        order = self.stuck(make_order(status=Status.EXPIRED, actor=Actor.SYSTEM))
        response, _ = self.retry(order, market.UNKNOWN)
        self.assertEqual(response.json()["result"], "waiting")
        order.refresh_from_db()
        self.assertEqual(order.sync_state, SyncState.RELEASE_PENDING)

    def test_retry_consume_for_a_paid_order_keeps_it_paid(self):
        order = paid()
        long_ago = timezone.now() - timedelta(hours=1)
        Order.objects.filter(pk=order.pk).update(collected_at=long_ago, consumption_started_at=long_ago,
                                                 sync_state=SyncState.NEEDS_ATTENTION)
        response, call = self.retry(order, market.DONE)
        self.assertEqual(response.json()["result"], "fixed")
        call.assert_called_once_with("consume", mock.ANY, 41)
        order.refresh_from_db()
        self.assertEqual((order.status, order.sync_state), (Status.COLLECTED, SyncState.SYNCED))

    def test_retry_refused_when_not_needed_or_not_supported(self):
        fine = make_order()
        response, call = self.retry(fine, market.DONE)
        self.assertEqual((response.status_code, response.json()["code"]), (409, "not_needed"))
        shop_cancelled = make_order()
        shop_cancelled = self.stuck(change_status(shop_cancelled, Status.CANCELLED, actor=Actor.SHOP,
                                                  reason="Ran out"))
        self.assertEqual(shop_cancelled.cancelled_by, CancelledBy.SHOP)
        response, call = self.retry(shop_cancelled, market.DONE)
        self.assertEqual((response.status_code, response.json()["code"]), (409, "cannot_retry"))
        call.assert_not_called()
        self.assertEqual(self.admin.post(f"/api/admin/orders/{uuid.uuid4()}/retry").status_code, 404)

    def test_total_is_a_decimal_string(self):
        self.assertEqual(Decimal(paid().total_cash), Decimal("3.00"))
