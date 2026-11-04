"""LP-12: Stock adjustments, their history and free stock."""

from datetime import timedelta
from unittest.mock import patch

from django.db import DatabaseError, IntegrityError, connection, transaction
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from .authentication import TokenUser
from .models import Listing, Shop, StockAdjustment
from .serializers import ListingSerializer
from .stock import free_quantity
from .stock_service import StockAdjustmentError, adjust_stock


def published_listing(shop, **overrides):
    start = timezone.now() + timedelta(days=1)
    fields = {
        "shop_id": shop.id,
        "title": "Croissant Box",
        "description": "Fresh pastries",
        "category": "Bakery",
        "original_price": "10.00",
        "discounted_price": "5.00",
        "total_quantity": 5,
        "pickup_address": "12 Bliss Street, Beirut",
        "pickup_start": start,
        "pickup_end": start + timedelta(hours=2),
        "status": Listing.Status.PUBLISHED,
        **overrides,
    }
    return Listing.objects.create(**fields)


class StockAdjustmentTestCase(TestCase):

    def setUp(self):
        self.client = APIClient()

        self.shop = Shop.objects.create(
            operator_user_id=10,
            name="Cedar Bakery",
            approval_status=Shop.ApprovalStatus.APPROVED,
        )
        self.other_shop = Shop.objects.create(
            operator_user_id=20,
            name="Other Bakery",
            approval_status=Shop.ApprovalStatus.APPROVED,
        )

        self.listing = published_listing(self.shop)
        self.url = self.url_for(self.listing)

    def url_for(self, listing):
        return reverse(
            "listing_stock_adjustments",
            kwargs={"pk": listing.pk},
        )

    def authenticate(self, user_id, role="SHOP_OPERATOR"):
        self.client.force_authenticate(
            user=TokenUser(
                id=user_id,
                role=role,
                is_staff=False,
            )
        )

    def post(self, data, url=None):
        return self.client.post(url or self.url, data, format="json")


class AdjustStockAPITests(StockAdjustmentTestCase):

    def test_owner_can_add_stock(self):
        self.authenticate(10)

        response = self.post({"quantity_change": 3, "reason": "Baked more"})

        self.assertEqual(response.status_code, 201)
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.total_quantity, 8)
        self.assertEqual(response.data["listing"]["total_quantity"], 8)
        self.assertEqual(response.data["listing"]["free_quantity"], 8)

        adjustment = StockAdjustment.objects.get()
        self.assertEqual(adjustment.listing_id, self.listing.id)
        self.assertEqual(adjustment.quantity_change, 3)
        self.assertEqual(adjustment.reason, "Baked more")
        self.assertEqual(adjustment.kind, StockAdjustment.Kind.MANUAL)
        self.assertEqual(adjustment.reservation_ref, "")
        self.assertEqual(adjustment.actor_user_id, 10)
        self.assertEqual(response.data["adjustment"]["id"], adjustment.id)

    def test_owner_can_remove_stock(self):
        self.authenticate(10)

        response = self.post({"quantity_change": -2, "reason": "Two dropped"})

        self.assertEqual(response.status_code, 201)
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.total_quantity, 3)

    def test_stock_can_be_removed_down_to_zero(self):
        self.authenticate(10)

        response = self.post({"quantity_change": -5, "reason": "Sold in store"})

        self.assertEqual(response.status_code, 201)
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.total_quantity, 0)

    def test_reason_is_trimmed(self):
        self.authenticate(10)

        self.post({"quantity_change": 1, "reason": "  One more  "})

        self.assertEqual(StockAdjustment.objects.get().reason, "One more")

    def test_terms_revision_is_not_changed(self):
        self.authenticate(10)
        revision = self.listing.terms_revision

        self.post({"quantity_change": 2, "reason": "Baked more"})

        self.listing.refresh_from_db()
        self.assertEqual(self.listing.terms_revision, revision)

    # ---------- Who may adjust ----------

    def test_unauthenticated_user_cannot_adjust(self):
        response = self.post({"quantity_change": 1, "reason": "x"})

        self.assertIn(response.status_code, [401, 403])
        self.assertEqual(StockAdjustment.objects.count(), 0)

    def test_customer_cannot_adjust(self):
        self.authenticate(10, role="CUSTOMER")

        response = self.post({"quantity_change": 1, "reason": "x"})

        self.assertEqual(response.status_code, 403)
        self.assertEqual(StockAdjustment.objects.count(), 0)

    def test_other_shop_cannot_adjust(self):
        self.authenticate(20)

        response = self.post({"quantity_change": 1, "reason": "x"})

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["code"], "not_owner")
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.total_quantity, 5)
        self.assertEqual(StockAdjustment.objects.count(), 0)

    def test_operator_without_shop_cannot_adjust(self):
        self.authenticate(30)

        response = self.post({"quantity_change": 1, "reason": "x"})

        self.assertEqual(response.status_code, 403)
        self.assertEqual(StockAdjustment.objects.count(), 0)

    def test_missing_listing_is_refused(self):
        self.authenticate(10)

        response = self.client.post(
            reverse("listing_stock_adjustments", kwargs={"pk": 99999}),
            {"quantity_change": 1, "reason": "x"},
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(StockAdjustment.objects.count(), 0)

    def test_client_cannot_choose_actor_kind_or_listing(self):
        self.authenticate(10)

        response = self.post(
            {
                "quantity_change": 1,
                "reason": "x",
                "actor_user_id": 20,
                "kind": "SHOP_CANCELLATION",
                "listing": 1,
            }
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            sorted(response.data["fields"]),
            ["actor_user_id", "kind", "listing"],
        )
        self.assertEqual(StockAdjustment.objects.count(), 0)

    # ---------- Rejection rules ----------

    def assert_refused(self, response, http_status, code=None):
        self.assertEqual(response.status_code, http_status)
        if code:
            self.assertEqual(response.data["code"], code)
        self.assertIn("detail", response.data)
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.total_quantity, 5)
        self.assertEqual(StockAdjustment.objects.count(), 0)

    def test_zero_change_is_refused(self):
        self.authenticate(10)

        response = self.post({"quantity_change": 0, "reason": "Nothing"})

        self.assert_refused(response, 400, "zero_change")

    def test_missing_reason_is_refused(self):
        self.authenticate(10)

        response = self.post({"quantity_change": 1})

        self.assert_refused(response, 400, "invalid")
        self.assertIn("reason", response.data["fields"])

    def test_blank_reason_is_refused(self):
        self.authenticate(10)

        response = self.post({"quantity_change": 1, "reason": "   "})

        self.assert_refused(response, 400, "invalid")
        self.assertIn("reason", response.data["fields"])

    def test_missing_quantity_is_refused(self):
        self.authenticate(10)

        response = self.post({"reason": "Baked more"})

        self.assert_refused(response, 400, "invalid")
        self.assertIn("quantity_change", response.data["fields"])

    def test_non_whole_quantity_is_refused(self):
        self.authenticate(10)

        for value in ("1.5", "two", True):
            with self.subTest(value=value):
                response = self.post({"quantity_change": value, "reason": "x"})
                self.assert_refused(response, 400, "invalid")

    def test_total_below_zero_is_refused(self):
        self.authenticate(10)

        response = self.post({"quantity_change": -6, "reason": "Too many"})

        self.assert_refused(response, 400, "below_zero")

    def test_total_below_committed_is_refused(self):
        self.authenticate(10)

        with patch(
            "marketplace.stock_service.committed_quantity",
            return_value=4,
        ):
            response = self.post({"quantity_change": -2, "reason": "Dropped"})

        self.assert_refused(response, 400, "below_committed")

    def test_total_down_to_committed_is_allowed(self):
        self.authenticate(10)

        with patch(
            "marketplace.stock_service.committed_quantity",
            return_value=4,
        ):
            response = self.post({"quantity_change": -1, "reason": "Dropped"})

        self.assertEqual(response.status_code, 201)
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.total_quantity, 4)

    def test_draft_listing_is_refused(self):
        self.listing.status = Listing.Status.DRAFT
        self.listing.save()
        self.authenticate(10)

        response = self.post({"quantity_change": 1, "reason": "x"})

        self.assert_refused(response, 409, "not_published")

    def test_withdrawn_listing_is_refused(self):
        self.listing.status = Listing.Status.WITHDRAWN
        self.listing.save()
        self.authenticate(10)

        response = self.post({"quantity_change": 1, "reason": "x"})

        self.assert_refused(response, 409, "not_published")

    def test_ended_pickup_is_refused(self):
        now = timezone.now()
        self.listing.pickup_start = now - timedelta(hours=3)
        self.listing.pickup_end = now - timedelta(minutes=1)
        self.listing.save()
        self.authenticate(10)

        response = self.post({"quantity_change": 1, "reason": "x"})

        self.assert_refused(response, 409, "pickup_ended")

    def test_adjustment_allowed_during_pickup_window(self):
        now = timezone.now()
        self.listing.pickup_start = now - timedelta(minutes=30)
        self.listing.pickup_end = now + timedelta(minutes=30)
        self.listing.save()
        self.authenticate(10)

        response = self.post({"quantity_change": 1, "reason": "x"})

        self.assertEqual(response.status_code, 201)


class AdjustStockTransactionTests(StockAdjustmentTestCase):

    def test_listing_row_is_locked(self):
        with CaptureQueriesContext(connection) as queries:
            adjust_stock(
                listing_id=self.listing.id,
                shop_id=self.shop.id,
                quantity_change=1,
                reason="x",
                actor_user_id=10,
            )

        self.assertTrue(
            any("FOR UPDATE" in q["sql"] for q in queries.captured_queries)
        )

    def test_stock_and_adjustment_are_saved_together(self):
        """If the adjustment cannot be saved, the stock is not changed."""

        with patch(
            "marketplace.stock_service.StockAdjustment.objects.create",
            side_effect=DatabaseError("boom"),
        ):
            with self.assertRaises(DatabaseError):
                adjust_stock(
                    listing_id=self.listing.id,
                    shop_id=self.shop.id,
                    quantity_change=3,
                    reason="x",
                    actor_user_id=10,
                )

        self.listing.refresh_from_db()
        self.assertEqual(self.listing.total_quantity, 5)
        self.assertEqual(StockAdjustment.objects.count(), 0)

    def test_rules_are_checked_on_the_locked_row(self):
        """A stale in-memory listing cannot bypass the checks."""

        Listing.objects.filter(pk=self.listing.pk).update(
            status=Listing.Status.WITHDRAWN,
        )

        with self.assertRaises(StockAdjustmentError) as caught:
            adjust_stock(
                listing_id=self.listing.id,
                shop_id=self.shop.id,
                quantity_change=1,
                reason="x",
                actor_user_id=10,
            )

        self.assertEqual(caught.exception.code, "not_published")

    def test_service_rejects_other_shop(self):
        with self.assertRaises(StockAdjustmentError) as caught:
            adjust_stock(
                listing_id=self.listing.id,
                shop_id=self.other_shop.id,
                quantity_change=1,
                reason="x",
                actor_user_id=20,
            )

        self.assertEqual(caught.exception.code, "not_owner")

    def test_service_rejects_zero_and_missing_reason(self):
        for change, reason, code in (
            (0, "x", "zero_change"),
            (1, "", "reason_required"),
            (1, "   ", "reason_required"),
            (1, None, "reason_required"),
        ):
            with self.subTest(change=change, reason=reason):
                with self.assertRaises(StockAdjustmentError) as caught:
                    adjust_stock(
                        listing_id=self.listing.id,
                        shop_id=self.shop.id,
                        quantity_change=change,
                        reason=reason,
                        actor_user_id=10,
                    )
                self.assertEqual(caught.exception.code, code)


class StockAdjustmentConstraintTests(StockAdjustmentTestCase):

    def test_database_rejects_zero_change(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            StockAdjustment.objects.create(
                listing=self.listing,
                quantity_change=0,
                reason="x",
                actor_user_id=10,
            )

    def test_database_rejects_empty_reason(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            StockAdjustment.objects.create(
                listing=self.listing,
                quantity_change=1,
                reason="",
                actor_user_id=10,
            )

    def test_one_adjustment_per_shop_cancellation(self):
        StockAdjustment.objects.create(
            listing=self.listing,
            quantity_change=-1,
            reason="Unavailable",
            kind=StockAdjustment.Kind.SHOP_CANCELLATION,
            reservation_ref="LP-ABC123",
            actor_user_id=10,
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            StockAdjustment.objects.create(
                listing=self.listing,
                quantity_change=-1,
                reason="Unavailable",
                kind=StockAdjustment.Kind.SHOP_CANCELLATION,
                reservation_ref="LP-ABC123",
                actor_user_id=10,
            )


class StockHistoryAPITests(StockAdjustmentTestCase):

    def test_owner_sees_history_newest_first(self):
        self.authenticate(10)
        self.post({"quantity_change": 3, "reason": "Baked more"})
        self.post({"quantity_change": -1, "reason": "One dropped"})

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [(a["quantity_change"], a["reason"]) for a in response.data],
            [(-1, "One dropped"), (3, "Baked more")],
        )
        self.assertEqual(response.data[0]["kind"], "MANUAL")
        self.assertEqual(response.data[0]["actor_user_id"], 10)

    def test_history_only_includes_this_listing(self):
        other_listing = published_listing(self.shop, title="Bread")
        self.authenticate(10)
        self.post({"quantity_change": 1, "reason": "Croissants"})
        self.post(
            {"quantity_change": 2, "reason": "Bread"},
            url=self.url_for(other_listing),
        )

        response = self.client.get(self.url)

        self.assertEqual([a["reason"] for a in response.data], ["Croissants"])

    def test_empty_history(self):
        self.authenticate(10)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, [])

    def test_draft_history_is_visible_to_owner(self):
        draft = Listing.objects.create(shop_id=self.shop.id, title="Draft")
        self.authenticate(10)

        response = self.client.get(self.url_for(draft))

        self.assertEqual(response.status_code, 200)

    def test_other_shop_cannot_see_history(self):
        self.authenticate(10)
        self.post({"quantity_change": 1, "reason": "Private reason"})
        self.authenticate(20)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 403)
        self.assertNotIn("Private reason", str(response.data))

    def test_customer_cannot_see_history(self):
        self.authenticate(10, role="CUSTOMER")

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 403)

    def test_unauthenticated_user_cannot_see_history(self):
        response = self.client.get(self.url)

        self.assertIn(response.status_code, [401, 403])


class FreeQuantityTests(StockAdjustmentTestCase):

    def test_free_equals_total_while_nothing_is_committed(self):
        self.assertEqual(free_quantity(self.listing), 5)

    def test_free_subtracts_committed(self):
        with patch("marketplace.stock.committed_quantity", return_value=2):
            self.assertEqual(free_quantity(self.listing), 3)

    def test_listing_serializer_includes_free_quantity(self):
        data = ListingSerializer(self.listing).data

        self.assertEqual(data["free_quantity"], 5)

    def test_free_quantity_cannot_be_written(self):
        draft = Listing.objects.create(shop_id=self.shop.id, title="Draft")
        self.authenticate(10)

        self.client.patch(
            reverse("listing_detail", kwargs={"pk": draft.pk}),
            {"free_quantity": 99},
            format="json",
        )

        draft.refresh_from_db()
        self.assertEqual(free_quantity(draft), 0)

    def test_my_listings_includes_free_quantity(self):
        self.authenticate(10)

        response = self.client.get(reverse("my_listings"))

        self.assertEqual(response.data[0]["free_quantity"], 5)
