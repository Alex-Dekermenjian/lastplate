"""LP-66: every kind of logo becomes a tidy square badge image."""

from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from PIL import Image, ImageDraw

from .logo_processing import CONTENT, DARK, SIZE, WHITE, prepare_logo
from .test_lp37_restaurant_apply import ApplicationTestCase, application


def upload(image, image_format="PNG"):
    buffer = BytesIO()
    image.save(buffer, format=image_format)
    return SimpleUploadedFile(f"logo.{image_format.lower()}", buffer.getvalue(), content_type=f"image/{image_format.lower()}")


def badge(image, image_format="PNG"):
    result = Image.open(BytesIO(prepare_logo(upload(image, image_format)).read()))
    result.load()
    return result


def close(a, b, tolerance=10):
    return all(abs(x - y) <= tolerance for x, y in zip(a, b))


def content_box(result, background):
    plain = Image.new("RGB", result.size, background)
    from PIL import ImageChops
    return ImageChops.difference(result.convert("RGB"), plain).convert("L").point(lambda v: 255 if v > 30 else 0).getbbox()


class LogoProcessingTests(SimpleTestCase):
    def assert_inside_the_circle(self, box):
        """The logo stays in the centred square that fits inside the round badge."""
        left, top, right, bottom = box
        margin = (SIZE - CONTENT) // 2 - 2
        self.assertGreaterEqual(left, margin)
        self.assertGreaterEqual(top, margin)
        self.assertLessEqual(right, SIZE - margin)
        self.assertLessEqual(bottom, SIZE - margin)

    def test_every_logo_becomes_a_small_square_png(self):
        result = badge(Image.new("RGB", (3600, 1500), (200, 30, 30)))

        self.assertEqual(result.format, "PNG")
        self.assertEqual(result.size, (SIZE, SIZE))

    def test_a_wide_word_logo_is_shown_whole_and_as_wide_as_fits(self):
        wide = Image.new("RGBA", (900, 200), (0, 0, 0, 0))
        ImageDraw.Draw(wide).rectangle((0, 50, 899, 150), fill=(255, 103, 31, 255))

        result = badge(wide)

        self.assertTrue(close(result.getpixel((5, 5)), WHITE))
        box = content_box(result, WHITE)
        self.assert_inside_the_circle(box)
        self.assertGreaterEqual(box[2] - box[0], CONTENT - 4)  # uses the full width

    def test_a_tall_logo_is_shown_whole(self):
        tall = Image.new("RGBA", (200, 900), (0, 0, 0, 0))
        ImageDraw.Draw(tall).rectangle((50, 0, 150, 899), fill=(20, 90, 200, 255))

        box = content_box(badge(tall), WHITE)

        self.assert_inside_the_circle(box)
        self.assertGreaterEqual(box[3] - box[1], CONTENT - 4)

    def test_a_white_logo_on_transparent_gets_a_dark_badge(self):
        white_logo = Image.new("RGBA", (400, 400), (0, 0, 0, 0))
        ImageDraw.Draw(white_logo).ellipse((80, 80, 320, 320), fill=(255, 255, 255, 255))

        result = badge(white_logo)

        self.assertTrue(close(result.getpixel((5, 5)), DARK))
        self.assertTrue(close(result.getpixel((SIZE // 2, SIZE // 2)), WHITE))

    def test_a_dark_logo_on_transparent_gets_a_white_badge(self):
        dark_logo = Image.new("RGBA", (400, 400), (0, 0, 0, 0))
        ImageDraw.Draw(dark_logo).ellipse((80, 80, 320, 320), fill=(30, 30, 30, 255))

        self.assertTrue(close(badge(dark_logo).getpixel((5, 5)), WHITE))

    def test_a_logo_with_its_own_colour_keeps_it_around_the_edges(self):
        square = Image.new("RGB", (400, 400), (218, 41, 28))
        ImageDraw.Draw(square).rectangle((150, 100, 250, 300), fill=(255, 199, 44))

        result = badge(square)

        self.assertTrue(close(result.getpixel((5, 5)), (218, 41, 28)))
        self.assertTrue(close(result.getpixel((SIZE // 2, SIZE // 2)), (255, 199, 44)))

    def test_big_empty_margins_are_trimmed_so_the_logo_is_big(self):
        margins = Image.new("RGB", (2000, 2000), WHITE)
        ImageDraw.Draw(margins).rectangle((950, 950, 1050, 1050), fill=(0, 120, 60))

        box = content_box(badge(margins), WHITE)

        self.assertGreaterEqual(box[2] - box[0], CONTENT - 4)

    def test_a_jpeg_photo_logo_works(self):
        photo = Image.new("RGB", (640, 480), (90, 140, 60))
        ImageDraw.Draw(photo).ellipse((100, 60, 540, 420), fill=(240, 220, 120))

        result = badge(photo, "JPEG")

        self.assertEqual(result.size, (SIZE, SIZE))
        self.assert_inside_the_circle(content_box(result, result.getpixel((0, 0))))

    def test_a_tiny_logo_is_scaled_into_the_badge_without_failing(self):
        result = badge(Image.new("RGB", (8, 8), (0, 0, 255)))

        self.assertEqual(result.size, (SIZE, SIZE))

    def test_a_fully_transparent_image_does_not_crash(self):
        result = badge(Image.new("RGBA", (300, 300), (0, 0, 0, 0)))

        self.assertEqual(result.size, (SIZE, SIZE))

    def test_a_sideways_phone_photo_is_turned_upright(self):
        # Stored sideways: a horizontal bar. Upright (as the phone meant it): vertical.
        sideways = Image.new("RGB", (400, 200), WHITE)
        ImageDraw.Draw(sideways).rectangle((20, 90, 380, 110), fill=(10, 10, 10))
        exif = Image.Exif()
        exif[0x0112] = 6  # "rotate 90°", as phones store it
        buffer = BytesIO()
        sideways.save(buffer, format="JPEG", exif=exif)
        uploaded = SimpleUploadedFile("logo.jpg", buffer.getvalue(), content_type="image/jpeg")

        result = Image.open(BytesIO(prepare_logo(uploaded).read()))
        left, top, right, bottom = content_box(result, WHITE)

        self.assertGreater(bottom - top, (right - left) * 4)


class TidyLogosCommandTests(TestCase):
    def test_old_logos_are_tidied_once_and_the_old_file_is_removed(self):
        from django.core.management import call_command
        from io import StringIO

        from .models import Shop
        from .test_browse import make_shop

        shop = make_shop()
        shop.logo.save("old.png", upload(Image.new("RGB", (900, 300), (200, 0, 0))))
        old_name = shop.logo.name

        call_command("tidy_logos", stdout=StringIO())
        shop.refresh_from_db()

        with shop.logo.open("rb") as file:
            self.assertEqual(Image.open(file).size, (SIZE, SIZE))
        self.assertFalse(shop.logo.storage.exists(old_name))

        out = StringIO()
        call_command("tidy_logos", stdout=out)
        self.assertIn("1 already tidy", out.getvalue())


class LogoUploadTests(ApplicationTestCase):
    def test_a_logo_sent_with_the_application_is_saved_tidied(self):
        from .models import Shop

        wide = Image.new("RGBA", (1200, 300), (0, 0, 0, 0))
        ImageDraw.Draw(wide).rectangle((0, 100, 1199, 200), fill=(255, 255, 255, 255))

        response = self.send(application(logo=upload(wide)))

        self.assertIn(response.status_code, (200, 201), response.content)
        shop = Shop.objects.get()
        with shop.logo.open("rb") as file:
            saved = Image.open(file)
            saved.load()
        self.assertEqual((saved.format, saved.size), ("PNG", (SIZE, SIZE)))
        self.assertTrue(close(saved.convert("RGB").getpixel((5, 5)), DARK))  # white logo, dark badge

    def test_a_bad_logo_is_still_refused_before_tidying(self):
        response = self.send(application(logo=SimpleUploadedFile("logo.png", b"not an image", content_type="image/png")))

        self.assertEqual(response.status_code, 400)
        self.assertIn("logo", response.json())
