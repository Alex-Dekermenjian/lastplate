
from io import BytesIO
from tempfile import TemporaryDirectory

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image
from rest_framework.test import APIClient

from .authentication import TokenUser
from .models import Listing, Shop


class PhotoUploadAPITests(TestCase):

    def setUp(self):
        self.temp_media = TemporaryDirectory()
        self.addCleanup(self.temp_media.cleanup)

        media_settings = override_settings(
            MEDIA_ROOT=self.temp_media.name
        )
        media_settings.enable()
        self.addCleanup(media_settings.disable)

        self.client = APIClient()

        self.shop = Shop.objects.create(
            operator_user_id=10,
            name="Cedar Bakery",
        )
        self.other_shop = Shop.objects.create(
            operator_user_id=20,
            name="Other Bakery",
        )

        self.listing = Listing.objects.create(
            shop_id=self.shop.id,
            title="Croissant Box",
        )

        self.url = reverse(
            "upload_listing_photo",
            kwargs={"pk": self.listing.pk},
        )

    def authenticate(self, user_id, role="SHOP_OPERATOR"):
        self.client.force_authenticate(
            user=TokenUser(
                id=user_id,
                role=role,
                is_staff=False,
            )
        )

    def make_photo(self):
        buffer = BytesIO()
        Image.new("RGB", (20, 20)).save(
            buffer,
            format="JPEG",
        )
        return SimpleUploadedFile(
            "food.jpg",
            buffer.getvalue(),
            content_type="image/jpeg",
        )

    def test_owner_can_upload_photo(self):
        self.authenticate(10)

        response = self.client.post(
            self.url,
            {"photo": self.make_photo()},
            format="multipart",
        )

        self.assertEqual(response.status_code, 200)

        self.listing.refresh_from_db()
        self.assertTrue(self.listing.photo)
        self.assertTrue(
            self.listing.photo.storage.exists(
                self.listing.photo.name
            )
        )

    def test_other_shop_cannot_upload(self):
        self.authenticate(20)

        response = self.client.post(
            self.url,
            {"photo": self.make_photo()},
            format="multipart",
        )

        self.assertEqual(response.status_code, 404)
        self.listing.refresh_from_db()
        self.assertFalse(self.listing.photo)

    def test_customer_cannot_upload(self):
        self.authenticate(30, role="CUSTOMER")

        response = self.client.post(
            self.url,
            {"photo": self.make_photo()},
            format="multipart",
        )

        self.assertEqual(response.status_code, 403)

    def test_unauthenticated_cannot_upload(self):
        response = self.client.post(
            self.url,
            {"photo": self.make_photo()},
            format="multipart",
        )

        self.assertIn(response.status_code, [401, 403])

    def test_invalid_photo_is_rejected(self):
        self.authenticate(10)

        invalid = SimpleUploadedFile(
            "fake.jpg",
            b"not a real image",
            content_type="image/jpeg",
        )

        response = self.client.post(
            self.url,
            {"photo": invalid},
            format="multipart",
        )

        self.assertEqual(response.status_code, 400)
        self.listing.refresh_from_db()
        self.assertFalse(self.listing.photo)

    def test_published_listing_cannot_replace_photo(self):
        self.listing.status = Listing.Status.PUBLISHED
        self.listing.save()

        self.authenticate(10)

        response = self.client.post(
            self.url,
            {"photo": self.make_photo()},
            format="multipart",
        )

        self.assertEqual(response.status_code, 404)
