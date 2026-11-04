
from io import BytesIO
from tempfile import TemporaryDirectory

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image

from .models import Listing
from .photo_service import replace_listing_photo


class PhotoServiceTests(TestCase):

    def setUp(self):
        self.temp_media = TemporaryDirectory()
        self.addCleanup(self.temp_media.cleanup)

        media_override = override_settings(
            MEDIA_ROOT=self.temp_media.name
        )
        media_override.enable()
        self.addCleanup(media_override.disable)

        self.listing = Listing.objects.create(
            shop_id=1,
            title="Croissant Box",
        )

    def make_image(self, filename="food.jpg"):
        buffer = BytesIO()
        Image.new("RGB", (20, 20)).save(
            buffer, format="JPEG"
        )

        return SimpleUploadedFile(
            filename,
            buffer.getvalue(),
            content_type="image/jpeg",
        )

    def test_valid_photo_upload(self):
        updated = replace_listing_photo(
            self.listing.pk,
            shop_id=1,
            uploaded_file=self.make_image(),
        )

        self.assertTrue(updated.photo.name)
        self.assertTrue(
            updated.photo.name.startswith("listing_photos/")
        )
        self.assertTrue(updated.photo.storage.exists(updated.photo.name))

    def test_valid_photo_replacement(self):
        first = replace_listing_photo(
            self.listing.pk, 1, self.make_image()
        )

        old_name = first.photo.name

        second = replace_listing_photo(
            self.listing.pk, 1, self.make_image()
        )

        self.assertNotEqual(old_name, second.photo.name)
        self.assertFalse(second.photo.storage.exists(old_name))
        self.assertTrue(
            second.photo.storage.exists(second.photo.name)
        )

    def test_invalid_photo_preserves_original(self):
        first = replace_listing_photo(
            self.listing.pk, 1, self.make_image()
        )

        original_name = first.photo.name

        invalid = SimpleUploadedFile(
            "fake.jpg",
            b"this is not a real image",
            content_type="image/jpeg",
        )

        from django.core.exceptions import ValidationError

        with self.assertRaises(ValidationError):
            replace_listing_photo(
                self.listing.pk, 1, invalid
            )

        self.listing.refresh_from_db()

        self.assertEqual(
            self.listing.photo.name,
            original_name,
        )
        self.assertTrue(
            self.listing.photo.storage.exists(original_name)
        )

    def test_other_shop_cannot_replace_photo(self):
        from .models import Listing

        with self.assertRaises(Listing.DoesNotExist):
            replace_listing_photo(
                self.listing.pk,
                shop_id=999,
                uploaded_file=self.make_image(),
            )

        self.listing.refresh_from_db()
        self.assertFalse(self.listing.photo)
