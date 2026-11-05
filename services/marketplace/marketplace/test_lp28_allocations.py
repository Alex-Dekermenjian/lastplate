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

    def allocate(self, ref="line-1", quantity=2, listing=None, lines=None, **headers):
        lines = lines or [{"reservation_ref": ref, "listing_id": (listing or self.listing).pk, "quantity": quantity}]
        return self.client.post(reverse("internal-allocations"), {"lines": lines}, format="json",
                                **(headers or RESERVATIONS))

    def release(self, ref="line-1", listing=None, **headers):
        return self.client.post(reverse("internal-allocation-release", args=[ref]),
                                {"listing_id": (listing or self.listing).pk}, format="json",
                                **(headers or RESERVATIONS))

    def free(self, listing=None):
        listing = listing or self.listing
        listing.refresh_from_db()
        return free_quantity(listing)


class AllocateTests(AllocationAPITestCase):
    def test_holds_units_and_returns_the_terms(self):
        response = self.allocate()

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["status"], "HELD")
        self.assertEqual(response.data["shop"], {"id": self.shop.id, "name": "Cedar Bakery",
                                                 "address": "12 Bliss Street, Beirut",
                                                 "operator_user_id": self.shop.operator_user_id})
        self.assertEqual(response.data["lines"][0]["unit_price"], "4.50")
        self.assertEqual(self.free(), 3)

    def test_a_retry_returns_the_same_allocations(self):
        self.allocate()
        response = self.allocate()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Allocation.objects.count(), 1)
        self.assertEqual(self.free(), 3)

    def test_all_lines_are_held_together(self):
        bread = make_listing(self.shop, total_quantity=1)
        response = self.allocate(lines=[
            {"reservation_ref": "a", "listing_id": self.listing.pk, "quantity": 2},
            {"reservation_ref": "b", "listing_id": bread.pk, "quantity": 1},
        ])

        self.assertEqual(response.status_code, 201)
        self.assertEqual([line["status"] for line in response.data["lines"]], ["HELD", "HELD"])
        self.assertEqual((self.free(), self.free(bread)), (3, 0))

    def test_one_short_line_fails_the_whole_order_and_holds_nothing(self):
        bread = make_listing(self.shop, total_quantity=1)
        lines = [
            {"reservation_ref": "a", "listing_id": self.listing.pk, "quantity": 2},
            {"reservation_ref": "b", "listing_id": bread.pk, "quantity": 2},
        ]
        response = self.allocate(lines=lines)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["status"], "REJECTED")
        self.assertEqual([line["reject_reason"] for line in response.data["lines"]],
                         ["other_line_failed", "not_enough_stock"])
        self.assertEqual((self.free(), self.free(bread)), (5, 1))
        # The stored answer is repeated, never re-evaluated.
        self.assertEqual(self.allocate(lines=lines).status_code, 409)

    def test_the_same_listing_on_two_lines_counts_once(self):
        response = self.allocate(lines=[
            {"reservation_ref": "a", "listing_id": self.listing.pk, "quantity": 3},
            {"reservation_ref": "b", "listing_id": self.listing.pk, "quantity": 3},
        ])
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.free(), 5)

    def test_two_restaurants_or_reused_refs_are_refused(self):
        other = make_listing(make_shop(operator_user_id=2, name="Olive Tree"))
        two_shops = [{"reservation_ref": "a", "listing_id": self.listing.pk, "quantity": 1},
                     {"reservation_ref": "b", "listing_id": other.pk, "quantity": 1}]
        self.assertEqual(self.allocate(lines=two_shops).status_code, 409)

        self.allocate(ref="a")
        mixed = [{"reservation_ref": "a", "listing_id": self.listing.pk, "quantity": 2},
                 {"reservation_ref": "new", "listing_id": self.listing.pk, "quantity": 1}]
        self.assertEqual(self.allocate(lines=mixed).status_code, 409)
        self.assertEqual(self.free(), 3)

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
                self.assertEqual(response.data["lines"][0]["reject_reason"], reason)

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
            line = {"reservation_ref": f"par-{n}", "listing_id": listing.pk, "quantity": 1}
            response = client.post(reverse("internal-allocations"), {"lines": [line]},
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
