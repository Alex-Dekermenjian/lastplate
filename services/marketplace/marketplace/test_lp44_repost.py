"""LP-44: repost an ended offer as today's draft."""

import io
import shutil
import tempfile
from datetime import datetime, timedelta
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.forms.models import model_to_dict
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from .listing_service import publish_listing
from .models import Listing
from .test_stock_adjustments import StockAdjustmentTestCase, published_listing

TINY_GIF = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,"
    b"\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
)


def tiny_png():
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), "orange").save(buffer, format="PNG")
    return SimpleUploadedFile("new.png", buffer.getvalue(), content_type="image/png")


def beirut(day, hour, minute=0):
    return timezone.make_aware(datetime.combine(day, datetime.min.time()).replace(hour=hour, minute=minute))


class RepostTestCase(StockAdjustmentTestCase):
    """Today is 22 November 2026 in Beirut. Yesterday's offer ran 17:00-20:30."""

    TODAY = datetime(2026, 11, 22).date()
    YESTERDAY = TODAY - timedelta(days=1)

    def setUp(self):
        super().setUp()
        self.branch = self.shop.branches.get()
        self.branch.address = "12 Bliss Street, Beirut"
        self.branch.save()
        self.ended = self.make_ended()
        self.listings_before = Listing.objects.count()

    def make_ended(self, start=None, end=None, **overrides):
        start = start or beirut(self.YESTERDAY, 17)
        end = end or beirut(self.YESTERDAY, 20, 30)
        with self.at(start - timedelta(hours=2)):
            return published_listing(
                self.shop,
                branch=self.branch,
                ingredients="Flour, butter",
                allergens="Gluten, milk",
                max_per_customer=2,
                total_quantity=7,
                pickup_start=start,
                pickup_end=end,
                **overrides,
            )

    def at(self, moment):
        return mock.patch("django.utils.timezone.now", return_value=moment)

    def repost(self, listing=None, now=None, data=None):
        url = reverse("listing_repost", kwargs={"pk": (listing or self.ended).pk})
        with self.at(now or beirut(self.TODAY, 12)):
            return self.client.post(url, data or {}, format="json")

    def snapshot(self, listing):
        listing.refresh_from_db()
        return {
            **model_to_dict(listing),
            "photo": listing.photo.name if listing.photo else "",
            "updated_at": listing.updated_at,
        }


class RepostCreatesTodaysDraftTests(RepostTestCase):

    def test_repost_creates_a_new_draft_and_leaves_the_old_offer_unchanged(self):
        self.authenticate(10)
        before = self.snapshot(self.ended)

        response = self.repost()

        self.assertEqual(response.status_code, 201, response.json())
        draft = Listing.objects.get(pk=response.json()["id"])
        self.assertNotEqual(draft.pk, self.ended.pk)
        self.assertEqual(draft.status, Listing.Status.DRAFT)
        self.assertEqual(Listing.objects.count(), self.listings_before + 1)
        self.assertEqual(self.snapshot(self.ended), before)

    def test_the_draft_copies_the_food_prices_and_branch(self):
        self.authenticate(10)

        draft = Listing.objects.get(pk=self.repost().json()["id"])
        self.ended.refresh_from_db()

        for field in ("shop_id", "branch_id", "kind", "title", "description", "category", "typical_contents",
                      "ingredients", "allergens", "original_price", "discounted_price", "max_per_customer",
                      "pickup_address"):
            self.assertEqual(getattr(draft, field), getattr(self.ended, field), field)

    def test_a_lastbag_stays_a_lastbag_with_its_typical_contents(self):
        bag = self.make_ended(kind=Listing.Kind.LASTBAG, typical_contents="Two pastries and bread")
        self.authenticate(10)

        draft = Listing.objects.get(pk=self.repost(bag).json()["id"])

        self.assertEqual(draft.kind, Listing.Kind.LASTBAG)
        self.assertEqual(draft.typical_contents, "Two pastries and bread")

    def test_the_draft_has_todays_pickup_times_and_an_empty_quantity(self):
        self.authenticate(10)

        response = self.repost(now=beirut(self.TODAY, 12))

        draft = Listing.objects.get(pk=response.json()["id"])
        self.assertEqual(draft.pickup_start, beirut(self.TODAY, 17))
        self.assertEqual(draft.pickup_end, beirut(self.TODAY, 20, 30))
        self.assertEqual(draft.total_quantity, 0)
        self.assertEqual(response.json()["total_quantity"], 0)
        self.assertEqual(response.json()["reserved_quantity"], 0)

    def test_an_offer_from_last_week_also_moves_to_today(self):
        old = self.make_ended(start=beirut(self.TODAY - timedelta(days=6), 9),
                              end=beirut(self.TODAY - timedelta(days=6), 11))
        self.authenticate(10)

        draft = Listing.objects.get(pk=self.repost(old, now=beirut(self.TODAY, 7)).json()["id"])

        self.assertEqual((draft.pickup_start, draft.pickup_end), (beirut(self.TODAY, 9), beirut(self.TODAY, 11)))

    def test_when_todays_pickup_end_has_passed_it_uses_tomorrow(self):
        self.authenticate(10)

        draft = Listing.objects.get(pk=self.repost(now=beirut(self.TODAY, 21)).json()["id"])

        tomorrow = self.TODAY + timedelta(days=1)
        self.assertEqual(draft.pickup_start, beirut(tomorrow, 17))
        self.assertEqual(draft.pickup_end, beirut(tomorrow, 20, 30))

    def test_exactly_at_todays_pickup_end_also_uses_tomorrow(self):
        self.authenticate(10)

        draft = Listing.objects.get(pk=self.repost(now=beirut(self.TODAY, 20, 30)).json()["id"])

        self.assertEqual(timezone.localdate(draft.pickup_start), self.TODAY + timedelta(days=1))

    def test_pickup_still_open_today_keeps_today(self):
        self.authenticate(10)

        draft = Listing.objects.get(pk=self.repost(now=beirut(self.TODAY, 18)).json()["id"])

        self.assertEqual(draft.pickup_end, beirut(self.TODAY, 20, 30))

    def test_a_window_past_midnight_still_ends_the_next_morning(self):
        late = self.make_ended(start=beirut(self.YESTERDAY, 22), end=beirut(self.TODAY, 1))
        self.authenticate(10)

        draft = Listing.objects.get(pk=self.repost(late, now=beirut(self.TODAY, 12)).json()["id"])

        self.assertEqual(draft.pickup_start, beirut(self.TODAY, 22))
        self.assertEqual(draft.pickup_end, beirut(self.TODAY + timedelta(days=1), 1))

    def test_a_taken_down_offer_can_be_reposted(self):
        self.ended.status = Listing.Status.WITHDRAWN
        self.ended.save()
        self.authenticate(10)

        response = self.repost()

        self.assertEqual(response.status_code, 201, response.json())
        self.ended.refresh_from_db()
        self.assertEqual(self.ended.status, Listing.Status.WITHDRAWN)

    def test_the_owner_enters_the_quantity_and_publishes_as_usual(self):
        self.authenticate(10)
        draft_id = self.repost().json()["id"]

        with self.at(beirut(self.TODAY, 12)):
            # An empty quantity can't be published.
            with self.assertRaises(Exception):
                publish_listing(draft_id, self.shop.id, shop_approved=True, profile_complete=True)
            edit = self.client.patch(reverse("listing_detail", kwargs={"pk": draft_id}),
                                     {"total_quantity": 4}, format="json")
            self.assertEqual(edit.status_code, 200, edit.json())
            published = publish_listing(draft_id, self.shop.id, shop_approved=True, profile_complete=True)

        self.assertEqual(published.status, Listing.Status.PUBLISHED)
        self.assertEqual(published.total_quantity, 4)


class RepostRefusedTests(RepostTestCase):

    def test_another_shop_cannot_repost_it(self):
        self.authenticate(20)
        before = self.snapshot(self.ended)

        response = self.repost()

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "not_owner")
        self.assertEqual(Listing.objects.count(), self.listings_before)
        self.assertEqual(self.snapshot(self.ended), before)

    def test_an_operator_without_a_shop_is_refused(self):
        self.authenticate(99)

        response = self.repost()

        self.assertEqual(response.status_code, 403)
        self.assertEqual(Listing.objects.count(), self.listings_before)

    def test_a_customer_is_refused(self):
        self.authenticate(10, role="CUSTOMER")

        self.assertEqual(self.repost().status_code, 403)
        self.assertEqual(Listing.objects.count(), self.listings_before)

    def test_signed_out_is_refused(self):
        self.assertIn(self.repost().status_code, (401, 403))
        self.assertEqual(Listing.objects.count(), self.listings_before)

    def test_a_live_offer_cannot_be_reposted(self):
        self.authenticate(10)

        response = self.repost(now=beirut(self.YESTERDAY, 18))

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "not_ended")
        self.assertEqual(Listing.objects.count(), self.listings_before)

    def test_a_draft_cannot_be_reposted(self):
        self.ended.status = Listing.Status.DRAFT
        self.ended.save()
        self.authenticate(10)

        response = self.repost()

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "not_ended")

    def test_an_offer_on_a_removed_branch_is_refused(self):
        self.branch.removed_at = timezone.now()
        self.branch.save()
        self.authenticate(10)

        response = self.repost()

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "branch_removed")
        self.assertEqual(Listing.objects.count(), self.listings_before)

    def test_fields_in_the_body_are_refused(self):
        self.authenticate(10)

        response = self.repost(data={"total_quantity": 5})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Listing.objects.count(), self.listings_before)


class RepostPhotoTests(RepostTestCase):

    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=self.media)
        override.enable()
        self.addCleanup(override.disable)
        super().setUp()
        self.ended.photo = SimpleUploadedFile("food.gif", TINY_GIF, content_type="image/gif")
        self.ended.save()

    def draft(self):
        self.authenticate(10)
        response = self.repost()
        self.assertEqual(response.status_code, 201, response.json())
        return Listing.objects.get(pk=response.json()["id"])

    def test_the_draft_has_its_own_copy_of_the_photo(self):
        draft = self.draft()

        self.assertTrue(draft.photo)
        self.assertNotEqual(draft.photo.name, self.ended.photo.name)
        storage = draft.photo.storage
        self.assertTrue(storage.exists(draft.photo.name))
        with storage.open(draft.photo.name, "rb") as copy:
            self.assertEqual(copy.read(), TINY_GIF)

    def test_deleting_the_draft_keeps_the_old_offers_photo(self):
        draft = self.draft()
        storage, old_name, copy_name = draft.photo.storage, self.ended.photo.name, draft.photo.name

        response = self.client.post(reverse("listing_delete_draft", kwargs={"pk": draft.pk}), {}, format="json")

        self.assertEqual(response.status_code, 204)
        self.assertFalse(storage.exists(copy_name))
        self.assertTrue(storage.exists(old_name))
        self.ended.refresh_from_db()
        self.assertEqual(self.ended.photo.name, old_name)

    def test_removing_the_old_photo_keeps_the_drafts_photo(self):
        draft = self.draft()
        storage = draft.photo.storage

        storage.delete(self.ended.photo.name)

        self.assertTrue(storage.exists(draft.photo.name))

    def test_replacing_the_drafts_photo_keeps_the_old_offers_photo(self):
        draft = self.draft()
        storage, old_name = draft.photo.storage, self.ended.photo.name

        response = self.client.post(
            reverse("upload_listing_photo", kwargs={"pk": draft.pk}),
            {"photo": tiny_png()},
            format="multipart",
        )

        self.assertIn(response.status_code, (200, 201), getattr(response, "data", None))
        self.assertTrue(storage.exists(old_name))

    def test_an_old_photo_file_that_is_gone_gives_a_draft_without_a_photo(self):
        self.ended.photo.storage.delete(self.ended.photo.name)

        draft = self.draft()

        self.assertFalse(draft.photo)
