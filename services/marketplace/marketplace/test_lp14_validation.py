"""LP-14: Input validation tests for the Marketplace APIs."""

from datetime import datetime, timedelta
from io import BytesIO
from zoneinfo import ZoneInfo

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from .image_validators import MAX_IMAGE_SIZE
from .models import Listing, StockAdjustment
from .stock_service import MAX_TOTAL_QUANTITY
from .test_lp14_permissions import (
    OPERATOR_A,
    PermissionTestCase,
    png_upload,
    published_listing,
)
from .test_shops import make_token


def image_bytes(fmt):
    buffer = BytesIO()
    Image.new("RGB", (8, 8), "red").save(buffer, format=fmt)
    return buffer.getvalue()


class ValidationTestCase(PermissionTestCase):

    def setUp(self):
        super().setUp()
        self.as_token(make_token(user_id=OPERATOR_A))
        self.draft = Listing.objects.create(shop_id=self.shop_a.id, title="A draft")

    def create(self, **fields):
        return self.client.post(reverse("listings"), {"title": "Box", **fields}, format="json")

    def edit(self, **fields):
        return self.client.patch(
            reverse("listing_detail", args=[self.draft.pk]), fields, format="json"
        )

    def assert_create_rejected(self, field, **fields):
        count = Listing.objects.count()
        response = self.create(**fields)
        self.assertEqual(response.status_code, 400, fields)
        self.assertIn(field, response.data)
        self.assertEqual(Listing.objects.count(), count)


class PriceValidationTests(ValidationTestCase):

    def test_prices_must_be_positive(self):
        for value in ("0", "0.00", "-1", "-0.01"):
            with self.subTest(value):
                self.assert_create_rejected("original_price", original_price=value)
                self.assert_create_rejected("discounted_price", discounted_price=value)

    def test_discount_must_be_below_original(self):
        for discounted in ("10.00", "10.01", "50"):
            with self.subTest(discounted):
                self.assert_create_rejected(
                    "discounted_price", original_price="10.00", discounted_price=discounted
                )

    def test_discount_rule_also_applies_to_edits_against_saved_values(self):
        Listing.objects.filter(pk=self.draft.pk).update(original_price="10.00")

        response = self.edit(discounted_price="12.00")

        self.assertEqual(response.status_code, 400)
        self.assertIn("discounted_price", response.data)

    def test_at_most_two_decimals(self):
        self.assert_create_rejected("original_price", original_price="10.999")
        self.assert_create_rejected("discounted_price", original_price="10", discounted_price="4.001")
        self.assertEqual(self.create(original_price="10.5", discounted_price="4.25").status_code, 201)

    def test_huge_and_non_numeric_prices_are_rejected(self):
        for value in ("123456789.00", "1e10", "NaN", "Infinity", "ten", True):
            with self.subTest(value):
                self.assert_create_rejected("original_price", original_price=value)


class QuantityValidationTests(ValidationTestCase):

    def test_quantity_must_be_a_non_negative_whole_number(self):
        for value in (-1, "2.5", "abc", True, ""):
            with self.subTest(value):
                self.assert_create_rejected("total_quantity", total_quantity=value)

    def test_huge_quantity_is_rejected_not_a_server_error(self):
        for value in (MAX_TOTAL_QUANTITY + 1, 10 ** 12, 10 ** 30):
            with self.subTest(value):
                self.assert_create_rejected("total_quantity", total_quantity=value)

    def test_publishing_needs_positive_stock(self):
        draft = Listing.objects.create(
            shop_id=self.shop_a.id,
            title="Box",
            description="d",
            category="c",
            pickup_address="Main street 1",
            original_price="10.00",
            discounted_price="5.00",
            total_quantity=0,
            pickup_start=timezone.now() + timedelta(hours=1),
            pickup_end=timezone.now() + timedelta(hours=2),
        )

        response = self.client.post(reverse("publish_listing", args=[draft.pk]))

        self.assertEqual(response.status_code, 400)
        draft.refresh_from_db()
        self.assertEqual(draft.status, Listing.Status.DRAFT)


class PickupTimeValidationTests(ValidationTestCase):

    def test_start_must_be_before_end(self):
        start = timezone.now() + timedelta(hours=3)
        for end in (start, start - timedelta(minutes=1)):
            with self.subTest(end=end):
                self.assert_create_rejected(
                    "pickup_end", pickup_start=start.isoformat(), pickup_end=end.isoformat()
                )

    def test_publishing_needs_a_future_end(self):
        draft = Listing.objects.create(
            shop_id=self.shop_a.id,
            title="Box",
            description="d",
            category="c",
            pickup_address="Main street 1",
            original_price="10.00",
            discounted_price="5.00",
            total_quantity=3,
            pickup_start=timezone.now() - timedelta(hours=2),
            pickup_end=timezone.now() - timedelta(minutes=1),
        )

        response = self.client.post(reverse("publish_listing", args=[draft.pk]))

        self.assertEqual(response.status_code, 400)
        draft.refresh_from_db()
        self.assertEqual(draft.status, Listing.Status.DRAFT)

    def test_times_without_an_offset_are_stored_as_beirut_time(self):
        # The web form sends datetime-local values (no offset). They are read
        # as the service's TIME_ZONE, never stored naive.
        response = self.create(pickup_start="2030-06-01T18:00", pickup_end="2030-06-01T20:00")

        self.assertEqual(response.status_code, 201)
        listing = Listing.objects.get(pk=response.data["id"])
        self.assertTrue(timezone.is_aware(listing.pickup_start))
        self.assertEqual(
            listing.pickup_start,
            datetime(2030, 6, 1, 18, 0, tzinfo=ZoneInfo("Asia/Beirut")),
        )

    def test_explicit_offsets_are_respected(self):
        response = self.create(
            pickup_start="2030-06-01T15:00:00Z", pickup_end="2030-06-01T20:00:00+03:00"
        )

        self.assertEqual(response.status_code, 201)
        listing = Listing.objects.get(pk=response.data["id"])
        self.assertEqual(listing.pickup_start, datetime(2030, 6, 1, 15, 0, tzinfo=ZoneInfo("UTC")))
        self.assertEqual(listing.pickup_end, datetime(2030, 6, 1, 17, 0, tzinfo=ZoneInfo("UTC")))

    def test_unparseable_times_are_rejected(self):
        self.assert_create_rejected("pickup_start", pickup_start="tomorrow evening")


class UnknownFieldTests(ValidationTestCase):

    def test_listing_create_rejects_unknown_and_read_only_fields(self):
        for field, value in (
            ("colour", "red"),
            ("shop", 1),
            ("free_quantity", 99),
            ("photo_display", "/media/x.png"),
            ("created_at", "2026-01-01T00:00:00Z"),
        ):
            with self.subTest(field):
                self.assert_create_rejected(field, **{field: value})

    def test_listing_edit_rejects_unknown_fields(self):
        response = self.edit(title="Renamed", colour="red")

        self.assertEqual(response.status_code, 400)
        self.assertIn("colour", response.data)
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.title, "A draft")

    def test_all_fields_the_web_form_sends_are_still_accepted(self):
        response = self.create(
            description="Fresh",
            category="Bakery",
            original_price="10.00",
            discounted_price="4.00",
            total_quantity="3",
            pickup_address="Main street 1",
            pickup_start="2030-06-01T18:00",
            pickup_end="2030-06-01T20:00",
            ingredients="Flour",
            allergens="Gluten",
        )

        self.assertEqual(response.status_code, 201, response.data)


class PhotoValidationTests(ValidationTestCase):

    def upload(self, *files, **extra):
        data = {"photo": list(files) if len(files) != 1 else files[0], **extra}
        return self.client.post(
            reverse("upload_listing_photo", args=[self.draft.pk]), data, format="multipart"
        )

    def assert_upload_rejected(self, *files, **extra):
        response = self.upload(*files, **extra)
        self.assertEqual(response.status_code, 400)
        self.draft.refresh_from_db()
        self.assertFalse(self.draft.photo)

    def test_fake_extension_and_wrong_type_are_rejected(self):
        cases = {
            "text named .jpg": SimpleUploadedFile("evil.jpg", b"<?php echo 1; ?>", "image/jpeg"),
            "html named .png": SimpleUploadedFile("x.png", b"<script>alert(1)</script>", "image/png"),
            "gif named .png": SimpleUploadedFile("x.png", image_bytes("GIF"), "image/png"),
            "bmp named .jpg": SimpleUploadedFile("x.jpg", image_bytes("BMP"), "image/jpeg"),
            "truncated png": SimpleUploadedFile("x.png", image_bytes("PNG")[:40], "image/png"),
            "empty file": SimpleUploadedFile("x.png", b"", "image/png"),
        }
        for label, upload in cases.items():
            with self.subTest(label):
                self.assert_upload_rejected(upload)

    def test_oversized_photo_is_rejected(self):
        big = SimpleUploadedFile(
            "big.png", image_bytes("PNG") + b"\0" * MAX_IMAGE_SIZE, "image/png"
        )

        self.assert_upload_rejected(big)

    def test_exactly_one_photo_and_nothing_else(self):
        self.assert_upload_rejected(png_upload(), png_upload("second.png"))
        self.assert_upload_rejected(png_upload(), status="PUBLISHED")

    def test_real_image_with_misleading_name_is_stored_under_a_random_name(self):
        response = self.upload(
            SimpleUploadedFile("../../evil.php.png", image_bytes("PNG"), "image/png")
        )

        self.assertEqual(response.status_code, 200)
        self.draft.refresh_from_db()
        self.assertRegex(self.draft.photo.name, r"^listing_photos/[0-9a-f]{32}\.png$")
        self.draft.photo.delete(save=False)


class PhoneValidationTests(ValidationTestCase):

    def test_public_contact_must_be_a_phone_number(self):
        for value in ("call me", "+961 1 23", "1234567890123456", "0000000", "+961<script>",
                      "961 1 234 567 ext 2", "+961 1\n234 567"):
            with self.subTest(value):
                response = self.client.patch(reverse("shop-me"), {"public_contact": value}, format="json")
                self.assertEqual(response.status_code, 400)
                self.assertIn("public_contact", response.data)
        self.shop_a.refresh_from_db()
        self.assertEqual(self.shop_a.public_contact, "+961 1 234 567")

    def test_common_formats_are_accepted(self):
        for value in ("+961 1 234 567", "01-234567", "(01) 234.567", "+96171234567"):
            with self.subTest(value):
                response = self.client.patch(reverse("shop-me"), {"public_contact": value}, format="json")
                self.assertEqual(response.status_code, 200)

    def test_surrounding_whitespace_is_trimmed_before_saving(self):
        response = self.client.patch(reverse("shop-me"), {"public_contact": " +961 1 234 568\n"}, format="json")

        self.assertEqual(response.status_code, 200)
        self.shop_a.refresh_from_db()
        self.assertEqual(self.shop_a.public_contact, "+961 1 234 568")


class StockAdjustmentValidationTests(ValidationTestCase):

    def setUp(self):
        super().setUp()
        self.offer = published_listing(self.shop_a, total_quantity=5)
        self.url = reverse("listing_stock_adjustments", args=[self.offer.pk])

    def adjust(self, **data):
        return self.client.post(self.url, data, format="json")

    def assert_refused(self, **data):
        response = self.adjust(**data)
        self.assertEqual(response.status_code, 400, data)
        self.offer.refresh_from_db()
        self.assertEqual(self.offer.total_quantity, 5)
        self.assertFalse(StockAdjustment.objects.filter(listing=self.offer).exists())
        return response

    def test_reason_length(self):
        self.assert_refused(quantity_change=1, reason="x" * 501)
        self.assert_refused(quantity_change=1, reason="   ")
        self.assertEqual(self.adjust(quantity_change=1, reason="x" * 500).status_code, 201)

    def test_zero_and_non_whole_changes(self):
        for value in (0, "0", "1.5", "one", True, None):
            with self.subTest(value):
                self.assert_refused(quantity_change=value, reason="Count")

    def test_huge_changes_are_refused_not_server_errors(self):
        for value in (MAX_TOTAL_QUANTITY, 10 ** 30, -(10 ** 30)):
            with self.subTest(value):
                self.assert_refused(quantity_change=value, reason="Count")
