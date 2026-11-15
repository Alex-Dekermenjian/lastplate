"""LP-80: expired-order strikes.

One test class per "Done when" line of the Jira card, plus its "Tests to
write" list. Times are set with a fake clock (explicit `now`, or a patched
timezone.now).
"""

import uuid
from datetime import timedelta
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .models import Actor, Order, Status, StrikeRemoval, SyncState
from .orders import change_status
from .strikes import BLOCK_FOR, STRIKE_WINDOW, remove_strike, strike_status
from .test_lp19_checkout import CUSTOMER_ID, CheckoutTestCase, PUBLIC_PEM, token
from .test_lp78_my_orders import make_order

NOW = timezone.now()


def expired(customer=CUSTOMER_ID, at=None):
    """One Expired order whose pick-up-by time was `at`."""
    order = make_order(customer=customer)
    Order.objects.filter(pk=order.pk).update(pick_up_by=at or NOW)
    order.refresh_from_db()
    return change_status(order, Status.EXPIRED, actor=Actor.SYSTEM, sync_state=SyncState.SYNCED)


def cancelled(customer=CUSTOMER_ID):
    order = make_order(customer=customer)
    return change_status(order, Status.CANCELLED, actor=Actor.CUSTOMER, sync_state=SyncState.SYNCED)


class EachExpiredOrderIsOneStrikeTests(TestCase):
    """Done when: each Expired order is 1 strike (cancelling never is);
    strikes older than 30 days don't count."""

    def test_each_expired_order_counts(self):
        expired(at=NOW - timedelta(days=3))
        expired(at=NOW - timedelta(days=1))
        expired(customer=999)
        self.assertEqual(strike_status(CUSTOMER_ID, now=NOW).count, 2)

    def test_cancelled_orders_never_count(self):
        for _ in range(5):
            cancelled()
        status = strike_status(CUSTOMER_ID, now=NOW)
        self.assertEqual((status.count, status.blocked_until), (0, None))

    def test_strike_older_than_30_days_ignored(self):
        expired(at=NOW - STRIKE_WINDOW - timedelta(minutes=1))
        expired(at=NOW - timedelta(days=29))
        self.assertEqual(strike_status(CUSTOMER_ID, now=NOW).count, 1)

    def test_reserved_and_paid_orders_never_count(self):
        make_order()
        make_order(status=Status.COLLECTED, actor=Actor.SHOP)
        self.assertEqual(strike_status(CUSTOMER_ID, now=NOW).count, 0)


class WarningAndBlockTests(TestCase):
    """Done when: at 2 strikes the customer sees a warning. At 3 strikes they
    cannot order for 7 days (they can still browse), and the block ends by itself."""

    def test_one_strike_no_warning_two_strikes_warning(self):
        expired(at=NOW - timedelta(days=2))
        self.assertFalse(strike_status(CUSTOMER_ID, now=NOW).warning)
        expired(at=NOW - timedelta(days=1))
        status = strike_status(CUSTOMER_ID, now=NOW)
        self.assertTrue(status.warning)
        self.assertIsNone(status.blocked_until)

    def test_three_expired_orders_block_ordering_for_7_days(self):
        third = NOW - timedelta(days=1)
        for at in (NOW - timedelta(days=10), NOW - timedelta(days=5), third):
            expired(at=at)
        self.assertEqual(strike_status(CUSTOMER_ID, now=NOW).blocked_until, third + BLOCK_FOR)
        # The block ends by itself, 7 days after the 3rd strike.
        self.assertEqual(strike_status(CUSTOMER_ID, now=third + BLOCK_FOR - timedelta(seconds=1)).blocked_until,
                         third + BLOCK_FOR)
        self.assertIsNone(strike_status(CUSTOMER_ID, now=third + BLOCK_FOR).blocked_until)

    def test_three_strikes_spread_over_more_than_30_days_never_block(self):
        for days in (40, 20, 1):
            expired(at=NOW - timedelta(days=days))
        self.assertIsNone(strike_status(CUSTOMER_ID, now=NOW).blocked_until)

    def test_block_runs_its_7_days_even_after_the_first_strike_leaves_the_window(self):
        third = NOW - timedelta(days=2)
        for at in (NOW - timedelta(days=29), NOW - timedelta(days=28), third):
            expired(at=at)
        later = NOW + timedelta(days=1, hours=12)  # the first strike is now 30.5 days old
        status = strike_status(CUSTOMER_ID, now=later)
        self.assertEqual((status.count, status.blocked_until), (2, third + BLOCK_FOR))


class RemoveStrikeTests(TestCase):
    """Done when: the admin can remove a strike with a reason."""

    def test_removed_strike_no_longer_counts_and_the_reason_is_kept(self):
        orders = [expired(at=NOW - timedelta(days=d)) for d in (3, 2, 1)]
        self.assertIsNotNone(strike_status(CUSTOMER_ID, now=NOW).blocked_until)
        removal = remove_strike(orders[1].reservation_ref, 77, "  Shop was closed when they came  ")
        self.assertEqual((removal.reason, removal.removed_by_user_id), ("Shop was closed when they came", 77))
        status = strike_status(CUSTOMER_ID, now=NOW)
        self.assertEqual((status.count, status.blocked_until), (2, None))

    def test_a_reason_is_required_and_only_expired_orders_once(self):
        order = expired()
        for reason in ("", "   ", None, 5):
            with self.assertRaisesMessage(Exception, "Say why"):
                remove_strike(order.reservation_ref, 77, reason)
        remove_strike(order.reservation_ref, 77, "Mistake")
        with self.assertRaisesMessage(Exception, "already removed"):
            remove_strike(order.reservation_ref, 77, "Again")
        with self.assertRaisesMessage(Exception, "No expired order"):
            remove_strike(cancelled().reservation_ref, 77, "Not a strike")
        self.assertEqual(StrikeRemoval.objects.count(), 1)

    def test_removals_are_never_deleted_or_edited(self):
        removal = remove_strike(expired().reservation_ref, 77, "Mistake")
        with self.assertRaises(Exception):
            removal.delete()
        with self.assertRaises(Exception):
            StrikeRemoval.objects.all().delete()
        removal.reason = "changed"
        with self.assertRaises(Exception):
            removal.save()


class CheckoutRefusesBlockedCustomersTests(CheckoutTestCase):
    """Done when: at 3 strikes ordering is refused until 7 days after the 3rd
    strike; browsing still works; then ordering works again."""

    def test_three_expired_orders_block_ordering_for_7_days(self):
        third = timezone.now() - timedelta(hours=1)
        for at in (third - timedelta(days=2), third - timedelta(days=1), third):
            expired(at=at)
        response = self.reserve()
        self.assertEqual((response.status_code, response.json()["code"]), (403, "customer_blocked"))
        self.assertIn("You can still browse", response.json()["detail"])
        self.assertEqual(self.market.free(41), 3)
        self.assertFalse(Order.objects.filter(status=Status.PROCESSING).exists())
        # Browsing the offer still works (Marketplace, no sign-in).
        self.assertEqual(self.market.read_offer(41)[0], 200)
        with mock.patch("django.utils.timezone.now", return_value=third + BLOCK_FOR + timedelta(seconds=1)):
            self.market.add(41, "Glazed donuts", "1.50", 3)
            self.assertEqual(self.reserve().status_code, 201)

    def test_two_strikes_still_order(self):
        expired(at=timezone.now() - timedelta(days=2))
        expired(at=timezone.now() - timedelta(days=1))
        self.assertEqual(self.reserve().status_code, 201)

    def test_my_orders_tells_the_customer_their_strikes(self):
        expired(at=timezone.now() - timedelta(days=2))
        expired(at=timezone.now() - timedelta(days=1))
        strikes = self.client.get("/api/reservations").json()["strikes"]
        self.assertEqual((strikes["count"], strikes["warning"], strikes["blocked_until"]), (2, True, None))


@override_settings(JWT_PUBLIC_KEY=PUBLIC_PEM)
class AdminStrikeApiTests(TestCase):
    def setUp(self):
        self.admin = APIClient()
        self.admin.credentials(HTTP_AUTHORIZATION="Bearer " + token(user_id=1, role="ADMIN"))

    def test_only_the_admin_sees_and_removes_strikes(self):
        order = expired()
        for role in ("CUSTOMER", "SHOP_OPERATOR"):
            other = APIClient()
            other.credentials(HTTP_AUTHORIZATION="Bearer " + token(role=role))
            self.assertEqual(other.get("/api/admin/customers").status_code, 403)
            self.assertEqual(other.post(f"/api/admin/strikes/{order.reservation_ref}/remove",
                                        {"reason": "x"}, format="json").status_code, 403)
        self.assertEqual(APIClient().get("/api/admin/customers").status_code, 401)

    def test_admin_lists_customers_and_their_expired_history(self):
        old = expired(at=timezone.now() - timedelta(days=40))
        recent = expired(at=timezone.now() - timedelta(days=1))
        cancelled()
        listed = self.admin.get("/api/admin/customers").json()["customers"]
        self.assertEqual([(c["customer_user_id"], c["count"]) for c in listed], [(CUSTOMER_ID, 1)])
        body = self.admin.get(f"/api/admin/customers/{CUSTOMER_ID}").json()
        self.assertEqual([(o["reservation_ref"], o["counts"]) for o in body["expired_orders"]],
                         [(str(recent.reservation_ref), True), (str(old.reservation_ref), False)])

    def test_admin_removes_a_strike_with_a_reason(self):
        order = expired()
        url = f"/api/admin/strikes/{order.reservation_ref}/remove"
        self.assertEqual(self.admin.post(url, {}, format="json").json()["code"], "reason_required")
        response = self.admin.post(url, {"reason": "Restaurant confirmed it was closed"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["customer"]["count"], 0)
        self.assertEqual(self.admin.post(url, {"reason": "again"}, format="json").status_code, 409)
        self.assertEqual(self.admin.post(f"/api/admin/strikes/{uuid.uuid4()}/remove", {"reason": "x"},
                                         format="json").status_code, 404)
        history = self.admin.get(f"/api/admin/customers/{CUSTOMER_ID}").json()["expired_orders"][0]
        self.assertEqual((history["counts"], history["removed"]["reason"], history["removed"]["by_user_id"]),
                         (False, "Restaurant confirmed it was closed", 1))
