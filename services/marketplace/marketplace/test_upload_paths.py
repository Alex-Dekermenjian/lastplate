
from django.test import SimpleTestCase

from .upload_paths import listing_photo_upload_path


class ListingPhotoPathTests(SimpleTestCase):

    def test_jpeg_filename(self):
        path = listing_photo_upload_path(
            None, "my food photo.jpeg"
        )

        self.assertTrue(path.startswith("listing_photos/"))
        self.assertTrue(path.endswith(".jpg"))
        self.assertNotIn("my food photo", path)

    def test_png_filename(self):
        path = listing_photo_upload_path(
            None, "croissant.png"
        )

        self.assertTrue(path.endswith(".png"))

    def test_unique_filenames(self):
        first = listing_photo_upload_path(None, "food.jpg")
        second = listing_photo_upload_path(None, "food.jpg")

        self.assertNotEqual(first, second)

    def test_unsafe_filename_not_reused(self):
        path = listing_photo_upload_path(
            None, "../../secret.jpg"
        )

        self.assertNotIn("secret", path)
        self.assertNotIn("..", path)
