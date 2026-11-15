"""LP-60: the admin suspends a restaurant or removes an offer.

One test class per "Done when" line of the Jira card (Marketplace part),
plus its "Tests to write" list: suspended shop's offers are hidden; only
staff can do this. Blocking customers is in Reservations.
"""

import uuid

from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from .allocation_service import consume, reserve
from .models import Allocation, Listing, Shop
from .stock import free_quantity
from .test_browse import OPERATOR_ID, make_listing, make_shop
from .test_shops import TEST_PUBLIC_KEY, ShopAPITestCase, make_token

ADMIN_ID = 99


@override_settings(JWT_PUBLIC_KEY=TEST_PUBLIC_KEY)
class ModerationTestCase(ShopAPITestCase):
    def setUp(self):
        self.shop = make_shop()
        self.listing = make_listing(self.shop, total_quantity=5)
        self.as_admin()

    def as_admin(self):
        self.as_user(make_token(user_id=ADMIN_ID, role="ADMIN"))

    def as_owner(self):
        self.as_user(make_token(user_id=OPERATOR_ID, role="SHOP_OPERATOR"))

    def suspend(self, reason="Several customers reported spoiled food"):
        return self.client.post(reverse("admin-shop-suspend", args=[self.shop.id]), {"reason": reason}, format="json")

    def remove(self, reason="Photo shows alcohol"):
        return self.client.post(reverse("admin-listing-remove", args=[self.listing.id]), {"reason": reason},
                                format="json")

    def public(self):
        client = APIClient()
        browse = [o["id"] for o in client.get(reverse("listings")).json()]
        detail = client.get(reverse("listing_detail", kwargs={"pk": self.listing.id})).status_code
        page = client.get(reverse("restaurant-page", args=[self.shop.id])).status_code
        return browse, detail, page


class SuspendShopTests(ModerationTestCase):
    """Done when: suspend a restaurant with a reason: its offers disappear and
    the owner sees the reason. Restore brings it back."""

    def test_suspended_shops_offers_are_hidden(self):
        self.assertEqual(self.public(), ([self.listing.id], 200, 200))
        response = self.suspend()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["suspension_reason"], "Several customers reported spoiled food")
        self.assertEqual(self.public(), ([], 404, 404))

    def test_the_owner_sees_the_reason(self):
        self.suspend()
        self.as_owner()
        body = self.client.get(reverse("shop-me")).json()
        self.assertIsNotNone(body["suspended_at"])
        self.assertEqual(body["suspension_reason"], "Several customers reported spoiled food")

    def test_restore_brings_it_back(self):
        self.suspend()
        response = self.client.post(reverse("admin-shop-restore", args=[self.shop.id]))
        self.assertEqual((response.status_code, response.json()["suspended_at"]), (200, None))
        self.assertEqual(self.public(), ([self.listing.id], 200, 200))
        self.assertEqual(self.client.post(reverse("admin-shop-restore", args=[self.shop.id])).status_code, 409)

    def test_no_new_reservations_but_existing_ones_stay_valid(self):
        held_ref = uuid.uuid4()
        reserve(held_ref, self.listing.id, 2, self.listing.terms_revision)
        self.suspend()
        allocation, _ = reserve(uuid.uuid4(), self.listing.id, 1, self.listing.terms_revision)
        self.assertEqual((allocation.status, allocation.reject_reason),
                         (Allocation.Status.REJECTED, Allocation.RejectReason.SHOP_SUSPENDED))
        # The customer who reserved before can still pick up and pay.
        sold, changed = consume(held_ref, self.listing.id)
        self.assertEqual((sold.status, changed), (Allocation.Status.CONSUMED, True))

    def test_a_suspended_shop_cannot_publish(self):
        draft = make_listing(self.shop, status=Listing.Status.DRAFT)
        self.suspend()
        self.as_owner()
        response = self.client.post(reverse("publish_listing", kwargs={"pk": draft.id}))
        self.assertEqual(response.status_code, 403)
        self.assertIn("suspended", response.json()["detail"])

    def test_a_reason_is_required_and_only_approved_shops(self):
        for reason in ("", "   "):
            self.assertEqual(self.suspend(reason).json()["code"], "reason_required")
        pending = make_shop(operator_user_id=5, approval_status=Shop.ApprovalStatus.PENDING)
        response = self.client.post(reverse("admin-shop-suspend", args=[pending.id]), {"reason": "x"}, format="json")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.suspend().status_code, 200)
        self.assertEqual(self.suspend().json()["code"], "already_suspended")
        self.assertEqual(self.client.post(reverse("admin-shop-suspend", args=[12345]), {"reason": "x"},
                                          format="json").status_code, 404)


class RemoveOfferTests(ModerationTestCase):
    """Done when: remove a single offer with a reason."""

    def test_removed_offer_is_hidden_and_the_owner_sees_why(self):
        other = make_listing(self.shop, title="Other offer")
        response = self.remove()
        self.assertEqual((response.status_code, response.json()["status"]), (200, Listing.Status.WITHDRAWN))
        browse, detail, page = self.public()
        self.assertEqual((browse, detail, page), ([other.id], 404, 200))
        self.as_owner()
        mine = {item["id"]: item for item in self.client.get(reverse("my_listings")).json()}
        self.assertEqual(mine[self.listing.id]["removal_reason"], "Photo shows alcohol")
        self.assertIsNone(mine[other.id]["removed_at"])

    def test_no_new_reservations_existing_ones_stay(self):
        held_ref = uuid.uuid4()
        reserve(held_ref, self.listing.id, 1, self.listing.terms_revision)
        self.remove()
        allocation, _ = reserve(uuid.uuid4(), self.listing.id, 1, self.listing.terms_revision)
        self.assertEqual(allocation.reject_reason, Allocation.RejectReason.NOT_PUBLISHED)
        self.assertEqual(consume(held_ref, self.listing.id)[0].status, Allocation.Status.CONSUMED)

    def test_reason_required_only_published_and_only_once(self):
        self.assertEqual(self.remove("  ").json()["code"], "reason_required")
        self.assertEqual(self.remove().status_code, 200)
        self.assertEqual(self.remove().json()["code"], "already_removed")
        draft = make_listing(self.shop, status=Listing.Status.DRAFT)
        response = self.client.post(reverse("admin-listing-remove", args=[draft.id]), {"reason": "x"}, format="json")
        self.assertEqual(response.status_code, 404)

    def test_admin_sees_the_shops_offers_with_stock(self):
        make_listing(self.shop, status=Listing.Status.DRAFT, title="Secret draft")
        reserve(uuid.uuid4(), self.listing.id, 2, self.listing.terms_revision)
        body = self.client.get(reverse("admin-shop-listings", args=[self.shop.id])).json()
        self.assertEqual([(o["id"], o["free_quantity"]) for o in body], [(self.listing.id, 3)])
        self.assertEqual(free_quantity(Listing.objects.get(pk=self.listing.id)), 3)


class OnlyStaffTests(ModerationTestCase):
    """Tests to write: only staff can do this."""

    def urls(self):
        return [("post", reverse("admin-shop-suspend", args=[self.shop.id])),
                ("post", reverse("admin-shop-restore", args=[self.shop.id])),
                ("get", reverse("admin-shop-listings", args=[self.shop.id])),
                ("post", reverse("admin-listing-remove", args=[self.listing.id]))]

    def test_customers_and_restaurants_get_403_and_nobody_401(self):
        for role in ("CUSTOMER", "SHOP_OPERATOR"):
            self.as_user(make_token(user_id=OPERATOR_ID, role=role, is_staff=True))
            for method, url in self.urls():
                with self.subTest(role=role, url=url):
                    response = getattr(self.client, method)(url, {"reason": "x"}, format="json")
                    self.assertEqual(response.status_code, 403)
        self.client.credentials()
        for method, url in self.urls():
            self.assertEqual(getattr(self.client, method)(url, {"reason": "x"}, format="json").status_code, 401)
        self.shop.refresh_from_db()
        self.listing.refresh_from_db()
        self.assertIsNone(self.shop.suspended_at)
        self.assertEqual(self.listing.status, Listing.Status.PUBLISHED)
