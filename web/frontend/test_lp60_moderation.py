"""LP-60: the admin suspends/restores a restaurant, removes an offer, and
blocks/unblocks a customer from /console; owners and customers see why."""

from django.urls import reverse

from .services import ServiceUnavailable
from .test_accounts import WebAuthTestCase, patch_service
from .test_shop_profile import COMPLETE_SHOP
from .test_lp56_login_signup import CUSTOMER
from .test_lp57_console import ADMIN, SHOP, ConsoleTestCase
from .test_lp78_my_orders import EXPIRED, OrderPagesTestCase

APPROVED = {**SHOP, "approval_status": "APPROVED", "suspended_at": None, "suspension_reason": ""}
SUSPENDED = {**APPROVED, "suspended_at": "2026-11-15T10:00:00Z", "suspension_reason": "Spoiled food reports"}
OFFERS = [{"id": 41, "title": "Glazed donuts", "kind": "ITEM", "status": "PUBLISHED", "discounted_price": "1.50",
           "total_quantity": 5, "free_quantity": 3, "pickup_start": "2026-11-15T16:00:00Z",
           "pickup_end": "2026-11-15T19:00:00Z", "removed_at": None, "removal_reason": ""},
          {"id": 42, "title": "Old cake", "kind": "ITEM", "status": "WITHDRAWN", "discounted_price": "3.00",
           "total_quantity": 2, "free_quantity": 2, "pickup_start": "2026-11-15T16:00:00Z",
           "pickup_end": "2026-11-15T19:00:00Z", "removed_at": "2026-11-15T11:00:00Z",
           "removal_reason": "Photo shows alcohol"}]
CUSTOMER_DETAIL = {"customer_user_id": 501, "first_name": "Maya", "count": 0, "warning": False,
                   "blocked_until": None, "ordering_blocked_until": None, "blocked_because": None,
                   "admin_block": None, "expired_orders": []}


class RestaurantModerationTests(ConsoleTestCase):
    def setUp(self):
        self.sign_in_as(ADMIN)

    def page(self, shop):
        with patch_service("admin_shop", return_value=(200, shop)), \
                patch_service("admin_shop_listings", return_value=(200, OFFERS)):
            return self.client.get(reverse("console-restaurant", args=[3]))

    def test_approved_restaurant_shows_suspend_and_its_offers(self):
        response = self.page(APPROVED)
        self.assertContains(response, "Suspend restaurant")
        self.assertContains(response, "Glazed donuts")
        self.assertContains(response, reverse("console-remove-offer", args=[3, 41]))
        self.assertContains(response, "Removed 15 Nov 2026, 13:00: Photo shows alcohol")

    def test_suspended_restaurant_shows_reason_and_restore(self):
        response = self.page(SUSPENDED)
        self.assertContains(response, "Spoiled food reports")
        self.assertContains(response, "Restore restaurant")
        self.assertNotContains(response, "Suspend restaurant")

    def test_suspend_needs_a_reason(self):
        with patch_service("admin_suspend_shop") as api:
            self.client.post(reverse("console-suspend-restaurant", args=[3]), {"reason": "  "})
        api.assert_not_called()

    def test_suspend_restore_and_remove_call_marketplace(self):
        with patch_service("admin_suspend_shop", return_value=(200, SUSPENDED)) as suspend:
            response = self.client.post(reverse("console-suspend-restaurant", args=[3]), {"reason": "Spoiled food"})
        self.assertRedirects(response, reverse("console-restaurant", args=[3]), fetch_redirect_response=False)
        self.assertEqual(suspend.call_args.args[1:], (3, "Spoiled food"))
        with patch_service("admin_restore_shop", return_value=(200, APPROVED)) as restore:
            self.client.post(reverse("console-restore-restaurant", args=[3]))
        self.assertEqual(restore.call_args.args[1:], (3,))
        with patch_service("admin_remove_listing", return_value=(200, OFFERS[1])) as remove:
            self.client.post(reverse("console-remove-offer", args=[3, 41]), {"reason": "Wrong photo"})
        self.assertEqual(remove.call_args.args[1:], (41, "Wrong photo"))

    def test_an_unreachable_service_says_it_may_not_be_saved(self):
        with patch_service("admin_suspend_shop", side_effect=ServiceUnavailable("marketplace")):
            response = self.client.post(reverse("console-suspend-restaurant", args=[3]), {"reason": "x"}, follow=False)
        self.assertEqual(response.status_code, 302)

    def test_only_the_admin_can_moderate(self):
        self.sign_in_as(CUSTOMER)
        with patch_service("admin_suspend_shop") as suspend, patch_service("admin_block_customer") as block:
            self.client.post(reverse("console-suspend-restaurant", args=[3]), {"reason": "x"})
            self.client.post(reverse("console-block-customer", args=[501]), {"reason": "x", "until": "2026-12-01"})
        suspend.assert_not_called()
        block.assert_not_called()


class CustomerBlockConsoleTests(ConsoleTestCase):
    def setUp(self):
        self.sign_in_as(ADMIN)

    def test_find_a_customer_by_account_number(self):
        response = self.client.get(reverse("console-customers"), {"account": "#501"})
        self.assertRedirects(response, reverse("console-customer", args=[501]), fetch_redirect_response=False)

    def test_block_form_and_unblock_button(self):
        with patch_service("admin_customer", return_value=(200, CUSTOMER_DETAIL)):
            self.assertContains(self.client.get(reverse("console-customer", args=[501])), "Block customer")
        blocked = {**CUSTOMER_DETAIL, "ordering_blocked_until": "2026-11-30T22:00:00Z", "blocked_because": "admin",
                   "admin_block": {"blocked_until": "2026-11-30T22:00:00Z", "reason": "Fake orders"}}
        with patch_service("admin_customer", return_value=(200, blocked)):
            response = self.client.get(reverse("console-customer", args=[501]))
        self.assertContains(response, "Unblock")
        self.assertContains(response, "Fake orders")

    def test_block_and_unblock_call_reservations(self):
        with patch_service("admin_block_customer", return_value=(200, {})) as block:
            self.client.post(reverse("console-block-customer", args=[501]),
                             {"until": "2026-12-01", "reason": "Fake orders"})
        self.assertEqual(block.call_args.args[1:], (501, "2026-12-01", "Fake orders"))
        with patch_service("admin_unblock_customer", return_value=(200, {})) as unblock:
            self.client.post(reverse("console-unblock-customer", args=[501]))
        self.assertEqual(unblock.call_args.args[1:], (501,))

    def test_block_needs_a_day_and_a_reason(self):
        with patch_service("admin_block_customer") as block:
            self.client.post(reverse("console-block-customer", args=[501]), {"until": "", "reason": "x"})
            self.client.post(reverse("console-block-customer", args=[501]), {"until": "2026-12-01", "reason": ""})
        block.assert_not_called()


class CustomerSeesTheBlockTests(OrderPagesTestCase):
    def test_my_orders_says_until_when(self):
        data = {"orders": [EXPIRED], "strikes": {"count": 0, "warning": False, "blocked_until": None},
                "admin_block": {"blocked_until": "2026-11-30T22:00:00+00:00"}}
        with patch_service("my_orders", return_value=(200, data)):
            response = self.client.get(reverse("my-orders"))
        self.assertContains(response, "LastPlate has blocked ordering on your account until Tue 1 Dec, 00:00")
        self.assertContains(response, "You can still browse food")


class OwnerSeesWhyTests(WebAuthTestCase):
    """Done when: the owner sees the suspension reason (and why an offer was removed)."""

    def test_workspace_shows_the_suspension_and_removal_reasons(self):
        self.sign_in_session()
        shop = {**COMPLETE_SHOP, "suspended_at": "2026-11-15T10:00:00Z", "suspension_reason": "Spoiled food reports"}
        listings = [{"id": 1, "title": "Croissant Box", "status": "PUBLISHED", "total_quantity": 5,
                     "free_quantity": 5, "discounted_price": "5.00", "removed_at": None, "removal_reason": ""},
                    {"id": 2, "title": "Old cake", "status": "WITHDRAWN", "total_quantity": 2,
                     "discounted_price": "3.00", "removed_at": "2026-11-15T11:00:00Z",
                     "removal_reason": "Photo shows alcohol"}]
        with patch_service("my_shop", return_value=(200, shop)), \
                patch_service("my_listings", return_value=(200, listings)):
            response = self.client.get(reverse("workspace") + "?tab=ended")
            live = self.client.get(reverse("workspace"))
        self.assertContains(live, "LastPlate has suspended your restaurant")
        self.assertContains(live, "Spoiled food reports")
        self.assertContains(live, "Hidden from customers: LastPlate suspended your restaurant.")
        self.assertNotContains(live, "Customers can see this offer.")
        self.assertContains(response, "Removed by LastPlate: Photo shows alcohol")
