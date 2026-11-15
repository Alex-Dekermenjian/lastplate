"""LP-60 (Reservations part): the admin blocks or unblocks a customer from
ordering, and sees their expired-order history.

Tests to write: blocked customer cannot reserve; only staff can do this.
"""

from datetime import timedelta
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .blocks import block_customer, ordering_block
from .models import CustomerBlock, Status
from .orders import beirut
from .test_lp19_checkout import CUSTOMER_ID, CheckoutTestCase, PUBLIC_PEM, token
from .test_lp80_strikes import expired

ADMIN_ID = 1


def in_days(days):
    return (beirut(timezone.now()) + timedelta(days=days)).date().isoformat()


def admin_client():
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION="Bearer " + token(user_id=ADMIN_ID, role="ADMIN"))
    return client


class BlockedCustomerCannotReserveTests(CheckoutTestCase):
    def setUp(self):
        super().setUp()
        self.admin = admin_client()

    def block(self, **data):
        return self.admin.post(f"/api/admin/customers/{CUSTOMER_ID}/block",
                               {"until": in_days(3), "reason": "Abusive to staff", **data}, format="json")

    def test_blocked_customer_cannot_reserve(self):
        self.assertEqual(self.block().status_code, 200)
        response = self.reserve()
        self.assertEqual((response.status_code, response.json()["code"]), (403, "customer_blocked"))
        self.assertIn("LastPlate has blocked ordering on your account", response.json()["detail"])
        self.assertEqual(self.market.free(41), 3)

    def test_unblock_lets_them_order_again(self):
        self.block()
        response = self.admin.post(f"/api/admin/customers/{CUSTOMER_ID}/unblock")
        self.assertEqual((response.status_code, response.json()["admin_block"]), (200, None))
        self.assertEqual(self.reserve().status_code, 201)
        self.assertEqual(self.admin.post(f"/api/admin/customers/{CUSTOMER_ID}/unblock").status_code, 409)

    def test_the_block_ends_on_the_chosen_day(self):
        self.block(until=in_days(2))
        until = CustomerBlock.objects.get().blocked_until
        self.assertEqual(ordering_block(CUSTOMER_ID, now=until - timedelta(seconds=1))[0], until)
        self.assertEqual(ordering_block(CUSTOMER_ID, now=until), (None, None))

    def test_the_customer_sees_until_when_but_not_the_reason(self):
        self.block()
        body = self.client.get("/api/reservations").json()
        self.assertIsNotNone(body["admin_block"]["blocked_until"])
        self.assertNotIn("reason", body["admin_block"])

    def test_a_reason_and_a_future_day_are_required(self):
        self.assertEqual(self.block(reason=" ").json()["code"], "reason_required")
        for until in (in_days(0), in_days(-1), "not a date", None):
            self.assertEqual(self.block(until=until).json()["code"], "invalid_date")
        self.assertFalse(CustomerBlock.objects.exists())

    def test_a_new_block_replaces_the_old_one_and_both_stay_on_record(self):
        self.block(until=in_days(10))
        self.block(until=in_days(2), reason="Shortened after appeal")
        self.assertEqual(CustomerBlock.objects.count(), 2)
        self.assertEqual(CustomerBlock.objects.filter(lifted_at__isnull=True).get().reason, "Shortened after appeal")
        with self.assertRaises(Exception):
            CustomerBlock.objects.all().delete()


@override_settings(JWT_PUBLIC_KEY=PUBLIC_PEM)
class AdminSeesCustomersTests(TestCase):
    def test_list_shows_blocked_customers_and_detail_shows_expired_history(self):
        block_customer(777, in_days(5), "Fake orders", ADMIN_ID)
        old = expired(at=timezone.now() - timedelta(days=2))
        admin = admin_client()
        listed = {c["customer_user_id"]: c for c in admin.get("/api/admin/customers").json()["customers"]}
        self.assertEqual(listed[777]["blocked_because"], "admin")
        self.assertEqual(listed[CUSTOMER_ID]["count"], 1)
        body = admin.get(f"/api/admin/customers/{CUSTOMER_ID}").json()
        self.assertEqual([o["reservation_ref"] for o in body["expired_orders"]], [str(old.reservation_ref)])
        self.assertEqual(body["admin_block"], None)
        self.assertEqual(admin.get("/api/admin/customers/777").json()["admin_block"]["reason"], "Fake orders")

    def test_strike_block_and_admin_block_the_later_end_wins(self):
        third = timezone.now() - timedelta(hours=1)
        for at in (third - timedelta(days=2), third - timedelta(days=1), third):
            expired(at=at)
        self.assertEqual(ordering_block(CUSTOMER_ID)[1], "strikes")
        block_customer(CUSTOMER_ID, in_days(20), "Repeated no-shows", ADMIN_ID)
        self.assertEqual(ordering_block(CUSTOMER_ID)[1], "admin")


@override_settings(JWT_PUBLIC_KEY=PUBLIC_PEM)
class OnlyStaffTests(TestCase):
    def test_only_the_admin_blocks_and_unblocks(self):
        for role in ("CUSTOMER", "SHOP_OPERATOR"):
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION="Bearer " + token(user_id=CUSTOMER_ID, role=role, is_staff=True))
            for path in ("block", "unblock"):
                response = client.post(f"/api/admin/customers/{CUSTOMER_ID}/{path}",
                                       {"until": in_days(3), "reason": "x"}, format="json")
                self.assertEqual(response.status_code, 403)
        self.assertEqual(APIClient().post(f"/api/admin/customers/{CUSTOMER_ID}/block").status_code, 401)
        self.assertFalse(CustomerBlock.objects.exists())
