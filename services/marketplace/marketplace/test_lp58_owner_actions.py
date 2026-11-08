"""LP-58: Mark sold out and Delete draft, from the owner's offer list."""

import shutil
import tempfile
from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from .browse import visible_listings
from .models import Listing, StockAdjustment
from .owner_actions import SOLD_OUT_REASON
from .stock import free_quantity
from .test_stock_adjustments import StockAdjustmentTestCase, published_listing

TINY_GIF = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,"
    b"\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
)


class MarkSoldOutTests(StockAdjustmentTestCase):

    def sold_out(self, listing=None, data=None):
        url = reverse("listing_sold_out", kwargs={"pk": (listing or self.listing).pk})
        return self.client.post(url, data or {}, format="json")

    def test_every_free_unit_goes_and_the_history_says_why(self):
        self.authenticate(10)

        response = self.sold_out()

        self.assertEqual(response.status_code, 201, response.json())
        self.listing.refresh_from_db()
        self.assertEqual(free_quantity(self.listing), 0)
        adjustment = StockAdjustment.objects.get(listing=self.listing)
        self.assertEqual(adjustment.quantity_change, -5)
        self.assertEqual(adjustment.reason, SOLD_OUT_REASON)
        self.assertEqual(adjustment.actor_user_id, 10)
        self.assertEqual(response.json()["listing"]["free_quantity"], 0)

    def test_a_sold_out_offer_leaves_the_public_pages(self):
        self.authenticate(10)
        self.assertIn(self.listing, [listing for listing, _ in visible_listings()])

        self.sold_out()

        self.assertNotIn(self.listing, [listing for listing, _ in visible_listings()])

    def test_twice_is_refused_the_second_time(self):
        self.authenticate(10)
        self.sold_out()

        response = self.sold_out()

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "already_sold_out")
        self.assertEqual(StockAdjustment.objects.count(), 1)

    def test_only_published_offers_whose_pickup_has_not_ended(self):
        self.authenticate(10)
        draft = published_listing(self.shop, status=Listing.Status.DRAFT)
        ended = published_listing(self.shop)
        Listing.objects.filter(pk=ended.pk).update(
            pickup_start=timezone.now() - timedelta(hours=3), pickup_end=timezone.now() - timedelta(hours=1),
        )

        self.assertEqual(self.sold_out(draft).json()["code"], "not_published")
        self.assertEqual(self.sold_out(ended).json()["code"], "pickup_ended")
        self.assertFalse(StockAdjustment.objects.exists())

    def test_only_the_owner(self):
        self.authenticate(20)

        response = self.sold_out()

        self.assertEqual(response.status_code, 403)
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.total_quantity, 5)

    def test_customers_and_signed_out_visitors_are_refused(self):
        self.assertEqual(self.sold_out().status_code, 401)
        self.authenticate(10, role="CUSTOMER")
        self.assertEqual(self.sold_out().status_code, 403)

    def test_no_fields_are_accepted(self):
        self.authenticate(10)

        response = self.sold_out(data={"quantity_change": -1})

        self.assertEqual(response.status_code, 400)
        self.assertFalse(StockAdjustment.objects.exists())


class DeleteDraftTests(StockAdjustmentTestCase):

    def setUp(self):
        super().setUp()
        self.draft = published_listing(self.shop, status=Listing.Status.DRAFT)

    def delete(self, listing):
        return self.client.post(reverse("listing_delete_draft", kwargs={"pk": listing.pk}), format="json")

    def test_the_owner_deletes_a_draft(self):
        self.authenticate(10)

        response = self.delete(self.draft)

        self.assertEqual(response.status_code, 204)
        self.assertFalse(Listing.objects.filter(pk=self.draft.pk).exists())

    def test_a_published_offer_is_never_deleted(self):
        self.authenticate(10)

        response = self.delete(self.listing)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "not_a_draft")
        self.assertTrue(Listing.objects.filter(pk=self.listing.pk).exists())

    def test_another_restaurants_draft_is_not_found(self):
        self.authenticate(20)

        self.assertEqual(self.delete(self.draft).status_code, 404)
        self.assertTrue(Listing.objects.filter(pk=self.draft.pk).exists())

    def test_customers_and_signed_out_visitors_are_refused(self):
        self.assertEqual(self.delete(self.draft).status_code, 401)
        self.authenticate(10, role="CUSTOMER")
        self.assertEqual(self.delete(self.draft).status_code, 403)
        self.assertTrue(Listing.objects.filter(pk=self.draft.pk).exists())

    def test_delete_on_the_offer_itself_is_still_refused(self):
        self.authenticate(10)

        response = self.client.delete(reverse("listing_detail", kwargs={"pk": self.draft.pk}))

        self.assertEqual(response.status_code, 405)
        self.assertTrue(Listing.objects.filter(pk=self.draft.pk).exists())

    def test_reading_and_editing_still_work_on_the_same_address(self):
        self.authenticate(10)
        url = reverse("listing_detail", kwargs={"pk": self.draft.pk})

        self.assertEqual(self.client.patch(url, {"title": "New"}, format="json").status_code, 200)
        self.assertEqual(self.client.get(reverse("listing_detail", kwargs={"pk": self.listing.pk})).status_code, 200)


class DeleteDraftPhotoTests(StockAdjustmentTestCase):

    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=self.media)
        override.enable()
        self.addCleanup(override.disable)
        super().setUp()

    def test_the_drafts_photo_file_goes_too(self):
        draft = published_listing(
            self.shop, status=Listing.Status.DRAFT,
            photo=SimpleUploadedFile("food.gif", TINY_GIF, content_type="image/gif"),
        )
        storage, name = draft.photo.storage, draft.photo.name
        self.assertTrue(storage.exists(name))
        self.authenticate(10)

        self.client.post(reverse("listing_delete_draft", kwargs={"pk": draft.pk}), format="json")

        self.assertFalse(storage.exists(name))
