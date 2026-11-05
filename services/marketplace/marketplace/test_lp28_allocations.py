"""LP-28: Reservations holds and releases stock through /internal/allocations.
A release frees units exactly once; only Reservations, with its key, may call."""

import threading
from datetime import timedelta

from django.db import connection
from django.test import TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from .models import Allocation, Listing, Shop
from .stock import free_quantity
from .test_browse import make_listing, make_shop
from .test_shops import TEST_PUBLIC_KEY, make_token

KEY = "test-only-reservations-key-0123456789abcdef"
KEYS = {"reservations": KEY}
RESERVATIONS = {"HTTP_X_SERVICE_NAME": "reservations", "HTTP_X_SERVICE_KEY": KEY}


@override_settings(INTERNAL_SERVICE_KEYS=KEYS, JWT_PUBLIC_KEY=TEST_PUBLIC_KEY)
class AllocationAPITestCase(APITestCase):
    def setUp(self):
        self.shop = make_shop()
        self.listing = make_listing(self.shop, total_quantity=5)

    def allocate(self, ref="line-1", quantity=2, listing=None, **headers):
        data = {"reservation_ref": ref, "listing_id": (listing or self.listing).pk, "quantity": quantity}
        return self.client.post(reverse("internal-allocations"), data, format="json", **(headers or RESERVATIONS))

    def release(self, ref="line-1", listing=None, **headers):
        return self.client.post(reverse("internal-allocation-release", args=[ref]),
                                {"listing_id": (listing or self.listing).pk}, format="json",
                                **(headers or RESERVATIONS))

    def free(self):
        self.listing.refresh_from_db()
        return free_quantity(self.listing)


class AllocateTests(AllocationAPITestCase):
    def test_holds_units_and_returns_the_terms(self):
        response = self.allocate()

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["status"], "HELD")
        self.assertEqual(response.data["unit_price"], "4.50")
        self.assertEqual(response.data["shop_id"], self.shop.id)
        self.assertEqual(response.data["shop_operator_user_id"], self.shop.operator_user_id)
        self.assertEqual(self.free(), 3)

    def test_a_retry_returns_the_same_allocation(self):
        self.allocate()
        response = self.allocate()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Allocation.objects.count(), 1)
        self.assertEqual(self.free(), 3)

    def test_not_enough_stock_is_a_stored_rejection(self):
        response = self.allocate(quantity=6)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["reject_reason"], "not_enough_stock")
        self.assertEqual(self.free(), 5)
        self.assertEqual(self.allocate(quantity=6).status_code, 409)

    def test_closed_offers_are_rejected(self):
        ended = timezone.now() - timedelta(hours=1)
        cases = {
            "not_published": make_listing(self.shop, status=Listing.Status.DRAFT),
            "pickup_ended": make_listing(self.shop, pickup_start=ended - timedelta(hours=1), pickup_end=ended),
            "shop_not_approved": make_listing(make_shop(operator_user_id=2,
                                                        approval_status=Shop.ApprovalStatus.REJECTED)),
        }
        for reason, listing in cases.items():
            with self.subTest(reason):
                response = self.allocate(ref=f"ref-{reason}", listing=listing)
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.data["reject_reason"], reason)

    def test_held_stock_hides_a_sold_out_offer_from_browsing(self):
        self.allocate(quantity=5)
        self.assertEqual(self.client.get(reverse("listings")).json(), [])


class ReleaseTests(AllocationAPITestCase):
    def test_release_frees_the_units_exactly_once(self):
        self.allocate()

        for _ in range(3):
            response = self.release()
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data["status"], "RELEASED")
        self.assertEqual(self.free(), 5)

    def test_releasing_an_unknown_ref_blocks_a_late_allocate(self):
        self.assertEqual(self.release(ref="late").status_code, 200)

        response = self.allocate(ref="late")

        self.assertEqual(response.data["status"], "RELEASED")
        self.assertEqual(self.free(), 5)

    def test_consumed_cannot_be_released(self):
        self.allocate()
        Allocation.objects.update(status=Allocation.Status.CONSUMED)

        self.assertEqual(self.release().status_code, 409)
        self.assertEqual(self.free(), 3)

    def test_wrong_listing_is_refused(self):
        self.allocate()
        other = make_listing(self.shop)

        self.assertEqual(self.release(listing=other).status_code, 409)
        self.assertEqual(self.free(), 3)


class InternalAuthTests(AllocationAPITestCase):
    def test_only_reservations_with_its_key(self):
        refused = [
            {},
            {"HTTP_X_SERVICE_NAME": "reservations", "HTTP_X_SERVICE_KEY": "wrong"},
            {"HTTP_X_SERVICE_NAME": "web", "HTTP_X_SERVICE_KEY": KEY},
            {"HTTP_AUTHORIZATION": f"Bearer {make_token(role='SHOP_OPERATOR')}"},
        ]
        for headers in refused:
            with self.subTest(headers=sorted(headers)):
                # An empty dict would fall back to the real key in the helper.
                headers = headers or {"HTTP_X_NOTHING": "1"}
                self.assertIn(self.allocate(**headers).status_code, (401, 403))
                self.assertIn(self.release(**headers).status_code, (401, 403))
        self.assertFalse(Allocation.objects.exists())

    @override_settings(INTERNAL_SERVICE_KEYS={"reservations": ""})
    def test_an_unconfigured_key_refuses_everything(self):
        self.assertEqual(self.allocate().status_code, 401)


@override_settings(INTERNAL_SERVICE_KEYS=KEYS)
class ConcurrentAllocationTests(TransactionTestCase):
    def test_parallel_orders_never_oversell(self):
        listing = make_listing(make_shop(), total_quantity=3)
        statuses = []

        def order(n):
            client = APIClient()
            response = client.post(reverse("internal-allocations"),
                                   {"reservation_ref": f"par-{n}", "listing_id": listing.pk, "quantity": 1},
                                   format="json", **RESERVATIONS)
            statuses.append(response.status_code)
            connection.close()

        threads = [threading.Thread(target=order, args=(n,)) for n in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(sorted(statuses), [201, 201, 201, 409, 409, 409])
        self.assertEqual(free_quantity(listing), 0)
