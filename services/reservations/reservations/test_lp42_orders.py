import uuid
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from .models import Actor, Order, Status
from .marketplace_client import MarketplaceUnavailable, operator_shop
from .test_lp19_checkout import PUBLIC_PEM, CheckoutTestCase, token
from .test_lp78_my_orders import make_order

URL = "/api/shop/reservations"
SHOP = {"id": 3, "branches": [{"id": 7, "name": "Hamra"}, {"id": 8, "name": "Verdun"}]}
NOW = datetime(2026, 11, 13, 10, tzinfo=dt_timezone.utc)


@override_settings(JWT_PUBLIC_KEY=PUBLIC_PEM)
class OwnerOrdersTests(TestCase):
    def setUp(self):
        self.api = APIClient()
        self.api.credentials(HTTP_AUTHORIZATION="Bearer " + token(user_id=71, role="SHOP_OPERATOR"))
        lookup = patch("reservations.shop_views.operator_shop", return_value=(200, SHOP))
        self.lookup = lookup.start()
        self.addCleanup(lookup.stop)
        clock = patch("reservations.shop_views.timezone.now", return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)

    def order(self, minutes=30, **fields):
        order = make_order()
        Order.objects.filter(pk=order.pk).update(pick_up_by=NOW + timedelta(minutes=minutes), **fields)
        order.refresh_from_db()
        return order

    def test_shop_and_branch_isolation_and_browser_shop_is_ignored(self):
        mine = self.order()
        self.order(shop_id=4, branch_id=9)
        self.order(shop_id=3, branch_id=9)  # inconsistent foreign branch must never leak
        body = self.api.get(URL, {"shop_id": 4}).json()
        self.assertEqual([o["reservation_ref"] for o in body["orders"]], [str(mine.reservation_ref)])
        self.lookup.assert_called_with(71)
        for branch in (9, "bad", -1):
            self.assertEqual(self.api.get(URL, {"branch_id": branch}).status_code, 404)

    def test_sort_search_branch_and_unfiltered_active_count(self):
        late = self.order(60, customer_first_name="Maya", pickup_code="ABC234")
        early = self.order(20, branch_id=8, customer_first_name="Jad", pickup_code="DEF567")
        overdue = self.order(-1)
        hidden = make_order(status=Status.PROCESSING)
        Order.objects.filter(pk=hidden.pk).update(pick_up_by=NOW + timedelta(minutes=15))
        body = self.api.get(URL).json()
        self.assertEqual([o["reservation_ref"] for o in body["orders"]],
                         [str(overdue.reservation_ref), str(early.reservation_ref), str(late.reservation_ref)])
        self.assertEqual(body["count"], 2)
        for params, expected in (({"q": "mAy"}, late), ({"q": "def"}, early), ({"branch_id": 8}, early)):
            body = self.api.get(URL, params).json()
            self.assertEqual([o["reservation_ref"] for o in body["orders"]], [str(expected.reservation_ref)])
            self.assertEqual(body["count"], 2)
        self.assertEqual(self.api.get(URL, {"q": "missing"}).json()["orders"], [])
        self.assertEqual(self.api.get(URL, {"count_only": "1"}).json()["orders"], [])
        self.assertEqual(self.api.get(URL).json()["orders"][0]["customer_first_name"], "Customer")

    def test_beirut_day_boundaries_not_utc_or_created_date(self):
        # November midnight in Lebanon is 22:00 UTC the previous day.
        start = datetime(2026, 11, 12, 22, tzinfo=dt_timezone.utc)
        included = []
        for delta in (-1, 0, 86399, 86400):
            order = self.order()
            Order.objects.filter(pk=order.pk).update(pick_up_by=start + timedelta(seconds=delta),
                                                     created_at=NOW - timedelta(days=1))
            if delta in (0, 86399):
                included.append(str(order.reservation_ref))
        self.assertEqual([o["reservation_ref"] for o in self.api.get(URL).json()["orders"]], included)

    def test_today_is_already_tomorrow_in_beirut_before_utc_midnight(self):
        now = datetime(2026, 11, 12, 22, 30, tzinfo=dt_timezone.utc)
        order = self.order()
        Order.objects.filter(pk=order.pk).update(pick_up_by=now + timedelta(minutes=15))
        with patch("reservations.shop_views.timezone.now", return_value=now):
            body = self.api.get(URL).json()
        self.assertEqual([o["reservation_ref"] for o in body["orders"]], [str(order.reservation_ref)])
        self.assertEqual(body["count"], 1)

    def test_terminal_orders_are_neither_listed_nor_counted(self):
        for status, actor in ((Status.COLLECTED, Actor.SHOP), (Status.CANCELLED, Actor.CUSTOMER),
                              (Status.EXPIRED, Actor.SYSTEM), (Status.NOT_RESERVED, Actor.SYSTEM)):
            order = make_order(status=status, actor=actor)
            Order.objects.filter(pk=order.pk).update(pick_up_by=NOW + timedelta(minutes=30))
        self.assertEqual(self.api.get(URL).json(), {"orders": [], "branches": SHOP["branches"], "count": 0})

    def test_deadline_equal_to_now_is_not_active(self):
        self.order(0)
        self.assertEqual(self.api.get(URL).json()["count"], 0)

    def test_authentication_and_roles(self):
        self.assertEqual(APIClient().get(URL).status_code, 401)
        for role in ("CUSTOMER", "ADMIN", ""):
            self.api.credentials(HTTP_AUTHORIZATION="Bearer " + token(
                role=role, exp=datetime.now(dt_timezone.utc) + timedelta(minutes=15)))
            self.assertEqual(self.api.get(URL).status_code, 403)
        self.api.credentials(HTTP_AUTHORIZATION="Bearer " + token(token_type="refresh"))
        self.assertEqual(self.api.get(URL).status_code, 401)
        self.lookup.assert_not_called()

    def test_missing_shop_and_service_faults_fail_closed(self):
        self.lookup.return_value = (404, {})
        self.assertEqual(self.api.get(URL).status_code, 404)
        for answer in ((403, {}), (200, {}), (200, {"id": 3, "branches": [None]})):
            self.lookup.return_value = answer
            self.assertEqual(self.api.get(URL).status_code, 503)
        self.lookup.side_effect = MarketplaceUnavailable()
        response = self.api.get(URL)
        self.assertEqual(response.status_code, 503)
        self.assertIn("no-store", response["Cache-Control"])

    @override_settings(MARKETPLACE_SERVICE_KEY="test-only")
    def test_lookup_uses_internal_api(self):
        with patch("reservations.marketplace_client._request", return_value=(200, SHOP)) as call:
            self.assertEqual(operator_shop(71), (200, SHOP))
        call.assert_called_once_with("GET", "/internal/shops/by-operator/71", internal=True)


class CheckoutNameTests(CheckoutTestCase):
    def test_processing_retry_preserves_name_and_allocates_only_once(self):
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + token(display_name="Maya Haddad"))
        self.market.allocate_down = True
        key = str(uuid.uuid4())
        self.assertEqual(self.reserve(key=key).status_code, 202)
        self.assertEqual(Order.objects.get().customer_first_name, "Maya")
        self.market.allocate_down = False
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + token(display_name="Changed Name"))
        self.assertEqual(self.reserve(key=key).status_code, 200)
        self.assertEqual(self.reserve(key=key).status_code, 200)
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(Order.objects.get().customer_first_name, "Maya")
        self.assertEqual(self.market.free(41), 2)

    def test_name_from_verified_claim_not_browser_and_retry_preserves_it(self):
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + token(display_name="  Maya   Haddad "))
        key = str(uuid.uuid4())
        body = {"request_key": key, "lines": self.lines(), "customer_first_name": "Forged"}
        self.assertEqual(self.client.post("/api/reservations", body, format="json").status_code, 201)
        self.assertEqual(Order.objects.get().customer_first_name, "Maya")
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + token(display_name="Changed Name"))
        self.assertEqual(self.client.post("/api/reservations", body, format="json").status_code, 200)
        self.assertEqual(Order.objects.get().customer_first_name, "Maya")

    def test_missing_claim_keeps_checkout_working(self):
        self.assertEqual(self.reserve().status_code, 201)
        self.assertEqual(Order.objects.get().customer_first_name, "")
