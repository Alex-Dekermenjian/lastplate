import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch

from django.db import IntegrityError, close_old_connections, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .collection import CollectionRefused, UNDO_WINDOW, claim_consumption, pay_or_undo
from .models import Actor, Order, Status, SyncState
from .orders import change_status, create_order
from .marketplace_client import MarketplaceUnavailable, operator_shop
from .test_lp19_checkout import CheckoutTestCase, PUBLIC_PEM, token

SHOP = {"id": 3, "branches": [{"id": 7, "name": "Hamra"}, {"id": 8, "name": "Verdun"}]}
BASE = "/api/shop/reservations"


def reserved(**fields):
    now = timezone.now()
    order = create_order(customer_user_id=501, shop_id=fields.pop("shop_id", 3),
                         branch_id=fields.pop("branch_id", 7), request_key=str(uuid.uuid4()),
                         customer_first_name="Maya", customer_phone="+961 70 123456",
                         lines=[{"listing_id": 41, "title": "Bread", "quantity": 2, "unit_price": "2.50",
                                 "pickup_start": now - timedelta(minutes=30),
                                 "pickup_end": now + timedelta(hours=2)}])
    order = change_status(order, Status.CONFIRMED, actor=Actor.SYSTEM, sync_state=SyncState.SYNCED)
    if fields:
        Order.objects.filter(pk=order.pk).update(**fields)
        order.refresh_from_db()
    return order


@override_settings(JWT_PUBLIC_KEY=PUBLIC_PEM)
class CollectionApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + token(role="SHOP_OPERATOR"))
        for path, kwargs in (("reservations.collection_views.operator_shop", {"return_value": (200, SHOP)}),
                             ("reservations.shop_views.operator_shop", {"return_value": (200, SHOP)}),
                             ("reservations.marketplace_client._request", {"side_effect": AssertionError("No stock HTTP in Paid/Undo")})):
            mock = patch(path, **kwargs)
            mock.start()
            self.addCleanup(mock.stop)
        self.order = reserved()

    def lookup(self, **changes):
        return self.client.post(BASE + "/lookup", {"branch_id": 7, "pickup_code": self.order.pickup_code, **changes}, format="json")

    def action(self, undo=False, version=0, **changes):
        payload = {"branch_id": 7, "version": version, "cash_received": True, **changes}
        return self.client.post(f"{BASE}/{self.order.reservation_ref}/{'undo' if undo else 'collect'}", payload, format="json")

    def test_lookup_summary_and_wrong_foreign_branch_and_foreign_shop(self):
        body = self.lookup(pickup_code=" " + self.order.pickup_code.lower() + " ").json()
        self.assertEqual((body["total_cash"], body["customer_phone"], body["lines"][0]["quantity"]),
                         ("5.00", "+961 70 123456", 2))
        for changes in ({"pickup_code": "BAD234"}, {"branch_id": 8}, {"branch_id": 999}, {"pickup_code": "oops"}):
            response = self.lookup(**changes)
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.json()["detail"], "No order with this code here")
        foreign = reserved(shop_id=4, branch_id=9)
        self.assertEqual(self.lookup(pickup_code=foreign.pickup_code, branch_id=9).status_code, 404)
        self.assertEqual(self.action(branch_id=8).status_code, 404)
        self.assertEqual(self.action(shop_id=4).status_code, 200)  # browser shop_id ignored

    def test_payment_records_cash_and_history_but_never_calls_consume(self):
        response = self.action()
        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual((self.order.status, self.order.sync_state), (Status.COLLECTED, SyncState.CONSUME_PENDING))
        self.assertIsNotNone(self.order.collected_at)
        self.assertIsNone(self.order.consumption_started_at)
        self.assertEqual(self.order.collection_version, 1)
        self.assertEqual(response.json()["status_label"], "Paid")
        self.assertEqual(response.json()["customer_phone"], "")
        self.assertEqual(self.order.events.filter(to_status=Status.COLLECTED).count(), 1)

    def test_duplicate_payment_is_noop_even_after_deadline(self):
        first = self.action().json()
        with patch("reservations.collection.timezone.now", return_value=timezone.now() + timedelta(hours=2)):
            second = self.action()
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["collected_at"], first["collected_at"])
        self.assertEqual(self.order.events.filter(to_status=Status.COLLECTED).count(), 1)

    def test_cash_confirmation_version_and_roles_are_required(self):
        self.assertEqual(self.action(cash_received=False).status_code, 400)
        for value in (None, True, -1, "0"):
            self.assertEqual(self.action(version=value).status_code, 400)
        for role in ("CUSTOMER", "ADMIN"):
            self.client.credentials(HTTP_AUTHORIZATION="Bearer " + token(role=role))
            self.assertEqual(self.lookup().status_code, 403)
            self.assertEqual(self.action().status_code, 403)
            self.assertEqual(self.action(undo=True).status_code, 403)
        self.client.credentials()
        self.assertEqual(self.lookup().status_code, 401)

    def test_expiry_boundary_returns_deadline_without_changing_state(self):
        for offset in (0, 1):
            with patch("reservations.collection.timezone.now", return_value=self.order.pick_up_by + timedelta(seconds=offset)):
                self.assertEqual(self.action().status_code, 409)
                response = self.lookup()
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.json()["pick_up_by"], self.order.pick_up_by.isoformat())
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Status.CONFIRMED)

    def test_rejects_before_pickup_start_and_unconfirmed_stock(self):
        with patch("reservations.collection.timezone.now", return_value=timezone.now() - timedelta(hours=1)):
            self.assertEqual(self.action().json()["code"], "too_early")
        Order.objects.filter(pk=self.order.pk).update(sync_state=SyncState.ALLOCATE_PENDING)
        self.assertEqual(self.action().json()["code"], "not_synced")

    def test_undo_window_and_stale_requests_after_undo(self):
        self.action()
        self.order.refresh_from_db()
        paid_at = self.order.collected_at
        with patch("reservations.collection.timezone.now", return_value=paid_at + UNDO_WINDOW):
            self.assertEqual(self.action(undo=True, version=1).json()["code"], "undo_closed")
        with patch("reservations.collection.timezone.now", return_value=paid_at + UNDO_WINDOW - timedelta(microseconds=1)):
            body = self.action(undo=True, version=1).json()
        self.assertEqual((body["status"], body["collection_version"], body["collected_at"]), ("CONFIRMED", 2, None))
        self.assertEqual(body["customer_phone"], "+961 70 123456")
        self.assertEqual(self.action(undo=True, version=1).status_code, 200)
        self.assertEqual(self.action(version=0).status_code, 409)
        self.assertEqual(self.action(version=2).status_code, 200)
        self.assertEqual(self.action(undo=True, version=1).status_code, 409)

    def test_undo_after_deadline_restores_overdue_reserved_without_extending_it(self):
        deadline = timezone.now() + timedelta(seconds=30)
        Order.objects.filter(pk=self.order.pk).update(pick_up_by=deadline)
        self.action()
        with patch("reservations.collection.timezone.now", return_value=deadline + timedelta(seconds=1)):
            self.assertEqual(self.action(undo=True, version=1).status_code, 200)
            self.assertEqual(self.action(version=2).status_code, 409)
        self.order.refresh_from_db()
        self.assertEqual(self.order.pick_up_by, deadline)

    def test_claim_timing_is_permanent_idempotent_and_disables_undo(self):
        self.action()
        self.order.refresh_from_db()
        at = self.order.collected_at + UNDO_WINDOW
        self.assertIsNone(claim_consumption(self.order.pk, at - timedelta(microseconds=1)))
        claimed = claim_consumption(self.order.pk, at)
        self.assertEqual(claimed.consumption_started_at, at)
        self.assertEqual(claim_consumption(self.order.pk, at + timedelta(minutes=1)).consumption_started_at, at)
        self.assertEqual(self.action(undo=True, version=1).json()["code"], "undo_closed")
        self.assertFalse(self.action().json()["can_undo"])

    def test_codes_are_protected_until_claim_and_survive_undo(self):
        self.action()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Order.objects.create(customer_user_id=999, shop_id=3, branch_id=7,
                                 request_key=str(uuid.uuid4()), pickup_code=self.order.pickup_code,
                                 total_cash="5.00", pick_up_by=self.order.pick_up_by)
        with patch("reservations.orders.new_pickup_code", side_effect=[self.order.pickup_code, "ZZZ234"]):
            other = reserved()
        self.assertEqual(other.pickup_code, "ZZZ234")
        self.assertEqual(self.action(undo=True, version=1).status_code, 200)
        self.action(version=2)
        self.order.refresh_from_db()
        claim_consumption(self.order.pk, self.order.collected_at + UNDO_WINDOW)
        with patch("reservations.orders.new_pickup_code", return_value=self.order.pickup_code):
            newer = reserved()
        self.assertEqual(newer.pickup_code, self.order.pickup_code)
        self.assertEqual(self.lookup().json()["reservation_ref"], str(newer.reservation_ref))

    def test_expired_status_and_foreign_references_cannot_be_collected(self):
        other = reserved(shop_id=4, branch_id=9)
        response = self.client.post(f"{BASE}/{other.reservation_ref}/collect",
                                   {"branch_id": 7, "version": 0, "cash_received": True}, format="json")
        self.assertEqual(response.status_code, 404)
        change_status(self.order, Status.EXPIRED, actor=Actor.SYSTEM)
        self.assertEqual(self.lookup().json()["code"], "pickup_expired")
        self.assertEqual(self.action().json()["code"], "pickup_expired")

    def test_phone_hidden_in_cancelled_and_expired_customer_responses(self):
        from .views import order_data
        for state in (Status.CANCELLED, Status.EXPIRED):
            order = reserved()
            updated = change_status(order, state, actor=Actor.CUSTOMER if state == Status.CANCELLED else Actor.SYSTEM)
            self.assertEqual(order_data(updated)["customer_phone"], "")

    def test_unavailable_ownership_service_never_discloses_order_details(self):
        with patch("reservations.collection_views.operator_shop", side_effect=MarketplaceUnavailable()):
            for response in (self.lookup(), self.action()):
                self.assertEqual(response.status_code, 503)
                for key in ("customer_phone", "lines", "total_cash", "reservation_ref"):
                    self.assertNotIn(key, response.json())

    @override_settings(MARKETPLACE_SERVICE_KEY="")
    def test_missing_service_key_has_a_safe_diagnostic(self):
        with self.assertLogs("reservations.marketplace", level="ERROR") as logs:
            with self.assertRaises(MarketplaceUnavailable):
                operator_shop(501)
        self.assertIn("service key is not configured", logs.output[0])

    def test_paid_list_customer_page_and_reserved_only_badge(self):
        self.action()
        self.assertEqual(self.client.get(BASE).json()["orders"], [])
        body = self.client.get(BASE, {"status": "COLLECTED"}).json()
        self.assertEqual((body["count"], body["orders"][0]["status_label"]), (0, "Paid"))
        self.assertEqual(body["orders"][0]["customer_phone"], "")
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + token(user_id=501, role="CUSTOMER"))
        body = self.client.get("/api/reservations").json()["orders"][0]
        self.assertEqual((body["status_label"], body["customer_phone"]), ("Paid", ""))


class PhoneCheckoutTests(CheckoutTestCase):
    def test_optional_phone_snapshot_and_duplicate_retry(self):
        body = {"request_key": str(uuid.uuid4()), "lines": self.lines(), "customer_phone": "+961 70 123456"}
        self.assertEqual(self.client.post("/api/reservations", body, format="json").status_code, 201)
        body["customer_phone"] = "12345"
        self.assertEqual(self.client.post("/api/reservations", body, format="json").status_code, 200)
        self.assertEqual(Order.objects.get().customer_phone, "+961 70 123456")

    def test_invalid_phone_does_not_allocate_or_create_order(self):
        for value in (None, "x" * 33, "<script>"):
            response = self.client.post("/api/reservations", {"request_key": str(uuid.uuid4()),
                                        "lines": self.lines(), "customer_phone": value}, format="json")
            self.assertEqual(response.status_code, 400)
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(self.market.calls, [])


class CollectionConcurrencyTests(TransactionTestCase):
    def worker(self, fn):
        close_old_connections()
        try:
            return fn()
        except CollectionRefused:
            return None
        finally:
            close_old_connections()

    def test_simultaneous_paid_is_one_transition(self):
        order = reserved()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(self.worker, [lambda: pay_or_undo(order.reservation_ref, 3, 7, 0)] * 2))
        self.assertTrue(all(results))
        self.assertEqual(order.events.filter(to_status=Status.COLLECTED).count(), 1)

    def test_claim_and_undo_cannot_both_win(self):
        order = reserved()
        paid = pay_or_undo(order.reservation_ref, 3, 7, 0)
        boundary = paid.collected_at + UNDO_WINDOW
        # Test the shared lock barrier with competing claim/undo attempts.
        with patch("reservations.collection.timezone.now", return_value=boundary):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(self.worker, [lambda: claim_consumption(order.pk),
                    lambda: pay_or_undo(order.reservation_ref, 3, 7, 1, undo=True)]))
        self.assertIsNotNone(results[0])
        self.assertIsNone(results[1])
        order.refresh_from_db()
        self.assertEqual(order.status, Status.COLLECTED)
        self.assertIsNotNone(order.consumption_started_at)
