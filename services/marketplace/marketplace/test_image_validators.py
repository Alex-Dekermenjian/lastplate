
from io import BytesIO

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from PIL import Image

from .image_validators import validate_listing_photo


class ListingPhotoValidationTests(SimpleTestCase):

    def make_image(self, format="JPEG"):
        buffer = BytesIO()
        Image.new("RGB", (100, 100)).save(
            buffer,
            format=format,
        )

        extension = "jpg" if format == "JPEG" else "png"

        return SimpleUploadedFile(
            f"food.{extension}",
            buffer.getvalue(),
            content_type=f"image/{format.lower()}",
        )

    def test_valid_jpeg(self):
        validate_listing_photo(self.make_image("JPEG"))

    def test_valid_png(self):
        validate_listing_photo(self.make_image("PNG"))

    def test_oversized_image(self):
        file = SimpleUploadedFile(
            "large.jpg",
            b"x" * (5 * 1024 * 1024 + 1),
            content_type="image/jpeg",
        )

        with self.assertRaises(ValidationError):
            validate_listing_photo(file)

    def test_fake_jpeg(self):
        file = SimpleUploadedFile(
            "fake.jpg",
            b"This is not an image",
            content_type="image/jpeg",
        )

        with self.assertRaises(ValidationError):
            validate_listing_photo(file)

    def test_unsupported_format(self):
        buffer = BytesIO()
        Image.new("RGB", (100, 100)).save(
            buffer,
            format="GIF",
        )

        file = SimpleUploadedFile(
            "food.gif",
            buffer.getvalue(),
            content_type="image/gif",
        )

        with self.assertRaises(ValidationError):
            validate_listing_photo(file)
