"""LP-79: the restaurant cancels an order (or all of a branch's for today)
with a reason; the food leaves stock instead of going back on sale.

Marketplace is LP-19's FakeMarketplace plus shop-cancel as LP-20 built it:
the held units leave total stock, so free stock does not go up
(test_lp20_stock_api covers the real endpoint and its retries).
"""

from datetime import datetime, timedelta, timezone as dt_timezone
from unittest import mock

from django.utils import timezone
from rest_framework.test import APIClient

from . import marketplace_client
from .cancellation import SHOP_REASONS, CancelRefused, cancel_all_for_today, shop_reason
from .models import Actor, CancelledBy, Order, Status, SyncState
from .orders import change_status
from .test_lp19_checkout import CheckoutTestCase, token
from .test_lp27_cancel import cancel_url as customer_cancel_url
from .test_lp78_my_orders import make_order

OPERATOR_ID = 71
SHOP = {"id": 3, "branches": [{"id": 7, "name": "Hamra"}, {"id": 8, "name": "Verdun"}]}
OTHER_SHOP = {"id": 4, "branches": [{"id": 9, "name": "Achrafieh"}]}
CANCEL_ALL = "/api/shop/reservations/cancel-all"
COUNTS = "/api/admin/shop-cancellations"


def shop_cancel_url(order):
    return f"/api/shop/reservations/{order.reservation_ref}/cancel"


class ShopCancelTestCase(CheckoutTestCase):
    def setUp(self):
        super().setUp()
        self.shop_cancel_down = False
        patcher = mock.patch.object(marketplace_client, "shop_cancel", self.fake_shop_cancel)
        patcher.start()
        self.addCleanup(patcher.stop)
        lookup = mock.patch("reservations.collection_views.operator_shop", return_value=(200, SHOP))
        self.lookup = lookup.start()
        self.addCleanup(lookup.stop)
        self.shop = APIClient()
        self.shop.credentials(HTTP_AUTHORIZATION="Bearer " + token(user_id=OPERATOR_ID, role="SHOP_OPERATOR"))

    def fake_shop_cancel(self, ref, listing_id, actor_user_id, reason):
        """Marketplace's shop-cancel: HELD → SHOP_CANCELLED, total goes down."""
        self.market.calls.append(("shop-cancel", listing_id, str(ref), actor_user_id, reason))
        if self.shop_cancel_down:
            return False
        with self.market.lock:
            allocation = self.market.allocations.get(ref)
            if allocation and allocation["status"] == "HELD":
                allocation["status"] = "SHOP_CANCELLED"
                self.market.offers[listing_id]["total"] -= allocation["quantity"]
            return True

    def place(self, *items):
        response = self.reserve(*(items or ((41, 2), (42, 1))))
        self.assertEqual(response.status_code, 201, response.content)
        return Order.objects.get(reservation_ref=response.json()["reservation_ref"])

    def cancel(self, order, reason="Food ran out", **extra):
        body = {} if reason is None else {"reason": reason}
        return self.shop.post(shop_cancel_url(order), {**body, **extra}, format="json")

    def calls(self, kind):
        return [call for call in self.market.calls if call[0] == kind]


class ShopCancelTests(ShopCancelTestCase):
    def test_reason_is_required(self):
        order = self.place()
        for reason in (None, "", "   ", "Because", 5, ["Food ran out"]):
            response = self.cancel(order, reason)
            self.assertEqual(response.status_code, 400, reason)
            self.assertEqual(response.json()["code"], "reason_required")
            self.assertEqual(response.json()["reasons"], list(SHOP_REASONS))
        order.refresh_from_db()
        self.assertEqual((order.status, order.cancelled_by), (Status.CONFIRMED, ""))
        self.assertEqual(self.calls("shop-cancel"), [])

    def test_only_the_three_reasons_are_accepted(self):
        self.assertEqual([shop_reason(f" {r} ") for r in SHOP_REASONS], ["Food ran out", "Closing early", "Other"])
        with self.assertRaises(CancelRefused):
            shop_reason("food ran out!")

    def test_cancel_records_shop_and_reason_and_food_is_not_put_back_on_sale(self):
        order = self.place()
        free_before = (self.market.free(41), self.market.free(42))
        self.assertEqual(free_before, (1, 4))

        response = self.cancel(order, "Closing early")

        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual((body["status"], body["cancelled_by"], body["cancel_reason"]),
                         ("CANCELLED", "Restaurant", "Closing early"))
        order.refresh_from_db()
        self.assertEqual((order.status, order.cancelled_by, order.cancel_reason, order.sync_state),
                         (Status.CANCELLED, CancelledBy.SHOP, "Closing early", SyncState.SYNCED))
        # Not returned to sale: no release, free stock unchanged, total stock down.
        self.assertEqual(self.calls("release"), [])
        self.assertEqual((self.market.free(41), self.market.free(42)), free_before)
        self.assertEqual((self.market.offers[41]["total"], self.market.offers[42]["total"]), (1, 4))
        self.assertEqual(len(self.calls("shop-cancel")), 2)  # one per item, with the operator and reason
        self.assertTrue(all(call[3:] == (OPERATOR_ID, "Closing early") for call in self.calls("shop-cancel")))
        event = order.events.order_by("-id").first()
        self.assertEqual((event.from_status, event.to_status, event.actor, event.reason),
                         (Status.CONFIRMED, Status.CANCELLED, Actor.SHOP, "Closing early"))

    def test_marketplace_down_leaves_it_for_the_retry_job(self):
        order = self.place()
        self.shop_cancel_down = True
        self.assertEqual(self.cancel(order).status_code, 200)
        order.refresh_from_db()
        self.assertEqual((order.status, order.sync_state), (Status.CANCELLED, SyncState.SHOP_CANCEL_PENDING))
        self.assertEqual(len(self.calls("shop-cancel")), 2)  # both items tried
        self.assertEqual(self.calls("release"), [])

    def test_shop_cannot_cancel_another_shops_order(self):
        order = self.place()
        for shop in (OTHER_SHOP, {"id": 3, "branches": [{"id": 8, "name": "Verdun"}]}):
            self.lookup.return_value = (200, shop)
            response = self.cancel(order)
            self.assertEqual(response.status_code, 404, shop)
            self.assertEqual(response.json()["code"], "not_found")
        self.lookup.assert_called_with(OPERATOR_ID)
        order.refresh_from_db()
        self.assertEqual(order.status, Status.CONFIRMED)
        self.assertEqual(self.calls("shop-cancel"), [])

    def test_customers_and_unknown_operators_are_refused(self):
        order = self.place()
        self.assertEqual(self.client.post(shop_cancel_url(order), {"reason": "Other"}, format="json").status_code, 403)
        self.lookup.return_value = (404, None)
        self.assertEqual(self.cancel(order).status_code, 404)
        self.lookup.side_effect = marketplace_client.MarketplaceUnavailable()
        self.assertEqual(self.cancel(order).status_code, 503)
        order.refresh_from_db()
        self.assertEqual(order.status, Status.CONFIRMED)

    def test_only_reserved_orders(self):
        order = self.place()
        self.assertEqual(self.cancel(order).status_code, 200)
        again = self.cancel(order)
        self.assertEqual((again.status_code, again.json()["code"]), (409, "already_cancelled"))
        self.assertEqual(len(self.calls("shop-cancel")), 2)  # the first press only
        self.assertEqual(order.events.filter(to_status=Status.CANCELLED).count(), 1)

        paid = make_order(status=Status.COLLECTED, actor=Actor.SHOP)
        response = self.cancel(paid)
        self.assertEqual((response.status_code, response.json()["code"]), (409, "already_paid"))
        expired = make_order(status=Status.EXPIRED, actor=Actor.SYSTEM)
        self.assertEqual(self.cancel(expired).json()["code"], "expired")
        hidden = make_order(status=Status.PROCESSING)
        self.assertEqual(self.cancel(hidden).status_code, 404)

    def test_not_after_pick_up_by(self):
        order = make_order()
        Order.objects.filter(pk=order.pk).update(pick_up_by=timezone.now() - timedelta(minutes=1))
        response = self.cancel(order)
        self.assertEqual((response.status_code, response.json()["code"]), (409, "pickup_time_passed"))
        order.refresh_from_db()
        self.assertEqual(order.status, Status.CONFIRMED)

    def test_customer_sees_the_shops_reason(self):
        order = self.place()
        self.cancel(order, "Food ran out")
        body = self.client.get(f"/api/reservations/{order.reservation_ref}").json()
        self.assertEqual((body["status_label"], body["cancelled_by"], body["cancel_reason"]),
                         ("Cancelled", "Restaurant", "Food ran out"))
        listed = self.client.get("/api/reservations").json()["orders"][0]
        self.assertEqual(listed["cancel_reason"], "Food ran out")
        # A customer can't cancel it again either.
        self.assertEqual(self.client.post(customer_cancel_url(order)).json()["code"], "already_cancelled")


# Midday in Beirut, so "today" is unambiguous.
NOW = datetime(2026, 11, 13, 10, tzinfo=dt_timezone.utc)


class CancelAllForTodayTests(ShopCancelTestCase):
    def setUp(self):
        super().setUp()
        clock = mock.patch("reservations.cancellation.timezone", mock.Mock(now=lambda: NOW))
        clock.start()
        self.addCleanup(clock.stop)

    def order(self, minutes, status=Status.CONFIRMED, **fields):
        order = make_order(status=status, **({"actor": Actor.SHOP} if status == Status.COLLECTED else {}))
        Order.objects.filter(pk=order.pk).update(pick_up_by=NOW + timedelta(minutes=minutes), **fields)
        order.refresh_from_db()
        return order

    def test_cancels_only_that_branchs_open_orders_for_today(self):
        first, second = self.order(30), self.order(9 * 60)  # 12:30 and 21:00 Beirut
        untouched = [
            self.order(-5),                          # pick-up-by already passed: will expire
            self.order(30, branch_id=8),             # another branch of the same shop
            self.order(30, shop_id=4, branch_id=7),  # another shop
            self.order(13 * 60),                     # tomorrow in Beirut (01:00)
            self.order(30, status=Status.COLLECTED), # already paid
        ]

        response = self.shop.post(CANCEL_ALL, {"branch_id": 7, "reason": "Closing early"}, format="json")

        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["cancelled"], 2)
        self.assertEqual({o["reservation_ref"] for o in body["orders"]},
                         {str(first.reservation_ref), str(second.reservation_ref)})
        for order in (first, second):
            order.refresh_from_db()
            self.assertEqual((order.status, order.cancelled_by, order.cancel_reason),
                             (Status.CANCELLED, CancelledBy.SHOP, "Closing early"))
        for order in untouched:
            before = order.status
            order.refresh_from_db()
            self.assertEqual(order.status, before)
        self.assertEqual(self.calls("release"), [])

    def test_reason_and_own_branch_are_required(self):
        order = self.order(30)
        for payload, code in (({"branch_id": 7}, 400), ({"branch_id": 7, "reason": "x"}, 400),
                              ({"branch_id": 9, "reason": "Other"}, 404), ({"reason": "Other"}, 404),
                              ({"branch_id": "7", "reason": "Other"}, 404)):
            self.assertEqual(self.shop.post(CANCEL_ALL, payload, format="json").status_code, code, payload)
        order.refresh_from_db()
        self.assertEqual(order.status, Status.CONFIRMED)

    def test_an_order_paid_meanwhile_is_skipped(self):
        first, second = self.order(30), self.order(60)
        real = change_status

        def pay_second_first(order, *args, **kwargs):
            if order.pk == first.pk:
                real(second, Status.COLLECTED, actor=Actor.SHOP, sync_state=SyncState.CONSUME_PENDING)
            return real(order, *args, **kwargs)

        with mock.patch("reservations.cancellation.change_status", pay_second_first):
            cancelled = cancel_all_for_today(SHOP, 7, OPERATOR_ID, "Food ran out", now=NOW)
        self.assertEqual([o.pk for o in cancelled], [first.pk])
        second.refresh_from_db()
        self.assertEqual(second.status, Status.COLLECTED)

    def test_nothing_to_cancel(self):
        response = self.shop.post(CANCEL_ALL, {"branch_id": 8, "reason": "Other"}, format="json")
        self.assertEqual(response.json(), {"cancelled": 0, "orders": []})


class ShopCancellationCountTests(ShopCancelTestCase):
    def test_admin_sees_the_count_per_restaurant(self):
        for _ in range(2):
            self.cancel(make_order(), "Food ran out")
        make_order(status=Status.CANCELLED, actor=Actor.CUSTOMER)  # customer's own: not counted
        make_order(status=Status.CANCELLED, actor=Actor.ADMIN, reason="Fraud")  # admin's: not counted
        other = make_order()
        Order.objects.filter(pk=other.pk).update(shop_id=4)
        change_status(other, Status.CANCELLED, actor=Actor.SHOP, reason="Other")

        admin = APIClient()
        admin.credentials(HTTP_AUTHORIZATION="Bearer " + token(user_id=1, role="ADMIN"))
        self.assertEqual(admin.get(COUNTS).json()["shops"],
                         [{"shop_id": 3, "shop_cancellations": 2}, {"shop_id": 4, "shop_cancellations": 1}])
        self.assertEqual(admin.get(COUNTS, {"shop_id": 3}).json()["shops"], [{"shop_id": 3, "shop_cancellations": 2}])
        self.assertEqual(admin.get(COUNTS, {"shop_id": 5}).json()["shops"], [{"shop_id": 5, "shop_cancellations": 0}])
        self.assertEqual(admin.get(COUNTS, {"shop_id": "x"}).status_code, 400)

    def test_only_the_admin(self):
        self.assertEqual(self.shop.get(COUNTS).status_code, 403)
        self.assertEqual(self.client.get(COUNTS).status_code, 403)
