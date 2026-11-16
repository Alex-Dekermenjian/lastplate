"""LP-33 reporting acceptance tests against the service's PostgreSQL DB."""
import csv
import io
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from .collection import pay_or_undo
from .marketplace_client import MarketplaceUnavailable
from .models import Actor, Order, Status, SyncState
from .orders import create_order, change_status
from .test_lp19_checkout import PUBLIC_PEM, token

NOW = datetime(2026, 11, 16, 12, tzinfo=dt_timezone.utc)
SHOP = {"id": 3, "branches": [{"id": 7, "name": "Hamra"}, {"id": 8, "name": "Removed branch"}]}
URL = "/api/shop/sales"
PARAMS = {"from": "2026-11-16", "to": "2026-11-16"}


@override_settings(JWT_PUBLIC_KEY=PUBLIC_PEM)
class SalesTests(TestCase):
    def setUp(self):
        self.api = APIClient()
        self.api.credentials(HTTP_AUTHORIZATION="Bearer " + token(role="SHOP_OPERATOR"))
        lookup = patch("reservations.sales.operator_shop", return_value=(200, SHOP))
        self.lookup = lookup.start()
        self.addCleanup(lookup.stop)
        clock = patch("reservations.sales.timezone.now", return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)

    def order(self, status=Status.CONFIRMED, branch=7, shop=3, deadline=None, multi=False):
        lines = [{"listing_id": 41, "quantity": 3, "title": "Food", "unit_price": Decimal("0.10"),
                  "pickup_start": NOW - timedelta(hours=1), "pickup_end": NOW + timedelta(hours=3)}]
        if multi:
            lines.append({**lines[0], "listing_id": 42, "quantity": 2, "unit_price": Decimal("1.25")})
        order = create_order(customer_user_id=1, shop_id=shop, branch_id=branch, request_key=str(uuid4()),
                             lines=lines, now=NOW - timedelta(minutes=10))
        if status != Status.PROCESSING:
            if status == Status.NOT_RESERVED:
                order = change_status(order, status, actor=Actor.SYSTEM, now=NOW)
            else:
                order = change_status(order, Status.CONFIRMED, actor=Actor.SYSTEM, sync_state=SyncState.SYNCED, now=NOW)
                if status != Status.CONFIRMED:
                    actor = {Status.COLLECTED: Actor.SHOP, Status.CANCELLED: Actor.CUSTOMER, Status.EXPIRED: Actor.SYSTEM}[status]
                    order = change_status(order, status, actor=actor, now=NOW, sync_state=SyncState.CONSUME_PENDING if status == Status.COLLECTED else SyncState.SYNCED)
        Order.objects.filter(pk=order.pk).update(pick_up_by=deadline or NOW + timedelta(minutes=30))
        order.refresh_from_db()
        return order

    def get(self, **params):
        response = self.api.get(URL, {**PARAMS, **params})
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def test_snapshots_statuses_multiple_lines_and_overdue_without_mutation(self):
        paid = self.order(Status.COLLECTED, multi=True)
        Order.objects.filter(pk=paid.pk).update(total_cash="999.99")  # snapshot lines are authoritative
        self.order()
        overdue = self.order(deadline=NOW)
        self.order(Status.EXPIRED)
        self.order(Status.CANCELLED)
        self.order(Status.PROCESSING)
        self.order(Status.NOT_RESERVED)
        data = self.get()
        self.assertEqual(data["summary"], {"orders": 5, "paid_orders": 1, "reserved_orders": 1,
                         "cancelled_orders": 1, "expired_orders": 2, "cash_collected": "2.80",
                         "cash_expected": "0.30", "items_saved": 5})
        overdue.refresh_from_db()
        self.assertEqual(overdue.status, Status.CONFIRMED)
        row = next(r for r in data["orders"] if r["reservation_ref"] == str(paid.reservation_ref))
        self.assertEqual((row["order_total"], row["quantity"]), ("2.80", 5))
        self.assertNotIn("customer_phone", row)

    def test_undo_payment_removes_cash_and_saved_items(self):
        order = self.order(Status.COLLECTED, multi=True)
        self.assertEqual(self.get()["summary"]["cash_collected"], "2.80")
        pay_or_undo(order.reservation_ref, 3, 7, 0, undo=True)
        summary = self.get()["summary"]
        self.assertEqual((summary["paid_orders"], summary["cash_collected"], summary["items_saved"], summary["cash_expected"]),
                         (0, "0.00", 0, "2.80"))

    def test_beirut_inclusive_dates_branch_and_shop_isolation(self):
        start = datetime(2026, 11, 15, 22, tzinfo=dt_timezone.utc)
        for seconds in (-1, 0, 86399, 86400):
            self.order(deadline=start + timedelta(seconds=seconds))
        self.order(branch=8)
        self.order(shop=4)
        self.order(branch=99)
        self.assertEqual(self.get()["summary"]["orders"], 3)
        self.assertEqual(self.get(branch="7", shop_id="4")["summary"]["orders"], 2)
        self.assertEqual(self.get(branch="8")["summary"]["orders"], 1)
        for branch in (99, "bad", -1):
            for endpoint in (URL, URL + "/export"):
                self.assertEqual(self.api.get(endpoint, {**PARAMS, "branch": branch}).status_code, 404)

    def test_invalid_filters_and_empty_money(self):
        self.assertEqual(self.get()["summary"]["cash_collected"], "0.00")
        for params in ({"from": "2026-02-30"}, {"from": "2026-11-17"}, {"to": "9999-12-31"},
                       {"from": "20261116"}, {"page": "0"}, {"page": "bad"}):
            self.assertEqual(self.api.get(URL, {**PARAMS, **params}).status_code, 400)

    def test_jwt_roles_and_failed_ownership_never_leak(self):
        for endpoint in (URL, URL + "/export"):
            self.assertEqual(APIClient().get(endpoint).status_code, 401)
            for claims, expected in (({"role": "CUSTOMER"}, 403), ({"role": "ADMIN"}, 403),
                                     ({"token_type": "refresh"}, 401),
                                     ({"exp": NOW - timedelta(days=1000)}, 401)):
                client = APIClient()
                client.credentials(HTTP_AUTHORIZATION="Bearer " + token(**{
                    "exp": datetime.now(dt_timezone.utc) + timedelta(minutes=15), **claims}))
                self.assertEqual(client.get(endpoint).status_code, expected)
        for answer in ((403, {}), (200, {}), (200, {"id": 3, "branches": [None]})):
            self.lookup.return_value = answer
            self.assertEqual(self.api.get(URL).status_code, 503)
        self.lookup.return_value = (404, {})
        self.assertEqual(self.api.get(URL).status_code, 404)
        self.lookup.side_effect = MarketplaceUnavailable()
        response = self.api.get(URL)
        self.assertEqual(response.status_code, 503)
        self.assertIn("no-store", response["Cache-Control"])

    def test_pagination_summary_and_csv_all_rows_filters_formula_safety(self):
        for _ in range(51):
            self.order(Status.COLLECTED)
        self.order(branch=8)
        self.order(shop=4)
        self.order(deadline=NOW - timedelta(days=1))
        self.assertEqual(len(self.get(branch=7)["orders"]), 50)
        data = self.get(branch=7, page=2)
        self.assertEqual(len(data["orders"]), 1)
        self.assertEqual((data["summary"]["paid_orders"], data["summary"]["cash_collected"]), (51, "15.30"))
        Order.objects.filter(shop_id=3, branch_id=7).update(branch_name="  =HYPERLINK(1)")
        response = self.api.get(URL + "/export", {**PARAMS, "branch": 7, "page": 2}, HTTP_ACCEPT="text/csv")
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response["Cache-Control"])
        rows = list(csv.DictReader(io.StringIO(response.content.decode())))
        self.assertEqual(len(rows), 51)
        self.assertEqual({row["branch_name"] for row in rows}, {"'  =HYPERLINK(1)"})
        self.assertEqual({row["cash_collected"] for row in rows}, {"0.30"})
        self.assertNotIn("customer", response.content.decode())

    def test_dashboard_summary_ignores_search_tabs_and_preserves_badge(self):
        self.order(Status.COLLECTED, multi=True)
        self.order()
        self.order(branch=8)
        self.order(deadline=NOW)
        with patch("reservations.shop_views.operator_shop", return_value=(200, SHOP)), \
             patch("reservations.shop_views.expire_due_orders_quietly"):
            summaries = []
            for extra in ({}, {"q": "missing"}, {"status": "COLLECTED"}):
                body = self.api.get("/api/shop/reservations", {"include_summary": "1", "branch_id": 7, **extra}).json()
                summaries.append(body["summary"])
                self.assertEqual(body["count"], 2)
            self.assertTrue(all(s == summaries[0] for s in summaries))
            self.assertEqual(summaries[0]["cash_collected"], "2.80")
            self.assertEqual(summaries[0]["cash_expected"], "0.30")
            self.assertEqual(summaries[0]["expired_orders"], 1)

    def test_default_day_csv_errors_and_read_only_methods(self):
        self.order(Status.COLLECTED)
        body = self.api.get(URL).json()
        self.assertEqual(body["filters"], {**PARAMS, "branch": None})
        self.assertEqual(body["summary"]["paid_orders"], 1)
        response = APIClient().get(URL + "/export", HTTP_ACCEPT="text/csv")
        self.assertEqual(response.status_code, 401)
        self.assertTrue(response["Content-Type"].startswith("application/json"))
        self.assertIn("detail", response.json())
        for endpoint in (URL, URL + "/export"):
            self.assertEqual(self.api.post(endpoint, {}, format="json").status_code, 405)

    def test_reporting_handles_more_than_worker_batch_of_overdue_orders(self):
        for _ in range(51):
            self.order(deadline=NOW)
        body = self.get()
        self.assertEqual(body["summary"]["expired_orders"], 51)
        self.assertEqual(body["summary"]["cash_expected"], "0.00")
        self.assertEqual(Order.objects.filter(status=Status.CONFIRMED).count(), 51)

    def test_dst_day_uses_local_midnights_and_payment_date_is_separate(self):
        # Beirut's spring clock change makes this calendar day 23 hours long.
        start = datetime(2026, 3, 28, 22, tzinfo=dt_timezone.utc)
        end = datetime(2026, 3, 29, 21, tzinfo=dt_timezone.utc)
        for deadline in (start - timedelta(seconds=1), start, end - timedelta(seconds=1), end):
            self.order(Status.COLLECTED, deadline=deadline)
        body = self.get(**{"from": "2026-03-29", "to": "2026-03-29"})
        self.assertEqual(body["summary"]["paid_orders"], 2)
        self.assertTrue(all(row["collected_at"].startswith("2026-11-16") for row in body["orders"]))
