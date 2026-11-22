"""LP-54: photo effect (Normal / Blurred / Blurred with magic) and the one-tap peek."""

import base64
import json
import statistics
from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.http import Http404
from django.test import RequestFactory
from django.urls import reverse
from PIL import Image

from .authentication import TokenUser
from .models import Listing, PhotoPeek
from .photo_effect_views import guarded_media
from .test_browse import OPERATOR_ID, MarketplaceTestCase, make_listing

NORMAL = Listing.PhotoEffect.NORMAL
BLURRED = Listing.PhotoEffect.BLURRED
MAGIC = Listing.PhotoEffect.MAGIC
CUSTOMER_ID = 4242
OTHER_CUSTOMER_ID = 4343

RED = (220, 30, 30)
BLUE = (30, 60, 220)


def photo_bytes(image_format="PNG"):
    """A 400x200 photo: red left half, blue right half, with a fine
    black-and-white checkerboard in the middle that a blur wipes out."""
    image = Image.new("RGB", (400, 200), RED)
    image.paste(BLUE, (200, 0, 400, 200))
    for x in range(150, 250):
        for y in range(50, 150):
            image.putpixel((x, y), (0, 0, 0) if (x // 2 + y // 2) % 2 else (255, 255, 255))
    buffer = BytesIO()
    image.save(buffer, format=image_format)
    return buffer.getvalue()


def photo_upload(name="dish.png"):
    return SimpleUploadedFile(name, photo_bytes(), content_type="image/png")


def decode_data_uri(uri):
    prefix = "data:image/png;base64,"
    assert uri.startswith(prefix), uri[:40]
    return Image.open(BytesIO(base64.b64decode(uri[len(prefix):]))).convert("RGBA")


class PhotoEffectTestCase(MarketplaceTestCase):

    def offer(self, effect, **overrides):
        """A published offer with a photo and this effect, as the owner would
        have saved it (the blurred copy is made by the same code)."""
        from .photo_effects import sync_blurred_photo

        listing = make_listing(self.shop, photo_effect=effect, **overrides)
        listing.photo.save("dish.png", photo_upload(), save=True)
        return sync_blurred_photo(listing, photo_changed=True)

    def as_customer(self, user_id=CUSTOMER_ID):
        self.client.force_authenticate(user=TokenUser(id=user_id, role="CUSTOMER", is_staff=False))

    def as_owner(self, user_id=OPERATOR_ID):
        self.client.force_authenticate(user=TokenUser(id=user_id, role="SHOP_OPERATOR", is_staff=False))

    def anonymous(self):
        self.client.force_authenticate(user=None)

    def customer_responses(self, listing):
        """Every public answer a customer's page is built from."""
        return [
            self.client.get(self.list_url),
            self.client.get(self.detail_url(listing)),
            self.client.get(reverse("restaurant-page", kwargs={"pk": self.shop.pk})),
        ]

    def shown_photo(self, response, listing):
        body = response.json()
        offers = body["offers"] if isinstance(body, dict) and "offers" in body else body
        if isinstance(offers, dict):
            offers = [offers]
        return next(offer for offer in offers if offer["id"] == listing.id)

    def peek_url(self, listing):
        return reverse("listing_peek", kwargs={"pk": listing.pk})

    def tap(self, listing, x=25, y=50):
        return self.client.post(self.peek_url(listing), {"x": x, "y": y}, format="json")


class CustomersSeeTheRightImageTests(PhotoEffectTestCase):

    def test_normal_shows_the_photo_itself(self):
        listing = self.offer(NORMAL)

        self.assertFalse(listing.photo_blurred)
        for response in self.customer_responses(listing):
            offer = self.shown_photo(response, listing)
            self.assertEqual(offer["photo_display"], listing.photo.url)
            self.assertEqual(offer["photo_effect"], NORMAL)

    def test_blurred_and_magic_show_only_the_blurred_copy(self):
        for effect in (BLURRED, MAGIC):
            with self.subTest(effect=effect):
                listing = self.offer(effect)

                self.assertTrue(listing.photo_blurred.name.startswith("listing_photos_blurred/"))
                for response in self.customer_responses(listing):
                    offer = self.shown_photo(response, listing)
                    self.assertEqual(offer["photo_display"], listing.photo_blurred.url)
                    self.assertEqual(offer["photo_effect"], effect)

    def test_the_originals_address_is_never_in_a_customer_response(self):
        for effect in (BLURRED, MAGIC):
            with self.subTest(effect=effect):
                listing = self.offer(effect)
                original = listing.photo.name.rsplit("/", 1)[-1]
                responses = self.customer_responses(listing)
                if effect == MAGIC:
                    self.as_customer()
                    responses += [
                        self.tap(listing),
                        self.tap(listing),  # the refused second tap
                        self.client.get(self.peek_url(listing)),
                        self.client.get(reverse("my_peeks"), {"listings": str(listing.pk)}),
                    ]
                    self.anonymous()
                for response in responses:
                    self.assertNotIn(original, response.content.decode())
                    self.assertNotIn(listing.photo.url, response.content.decode())

    def test_a_missing_blurred_copy_shows_no_photo_rather_than_the_original(self):
        listing = self.offer(MAGIC)
        Listing.objects.filter(pk=listing.pk).update(photo_blurred="")

        offer = self.shown_photo(self.client.get(self.detail_url(listing)), listing)

        self.assertIsNone(offer["photo_display"])

    def test_the_blurred_copy_is_strongly_blurred_and_the_same_shape(self):
        listing = self.offer(BLURRED)

        with listing.photo_blurred.open("rb") as file:
            blurred = Image.open(file).convert("L")
            blurred.load()
        self.assertEqual(blurred.size[0] * 200, blurred.size[1] * 400)

        # The fine checkerboard (pure black and white in the original) is gone:
        # what is left there is a nearly flat grey.
        scale = blurred.size[0] / 400
        box = (int(160 * scale), int(60 * scale), int(240 * scale), int(140 * scale))
        values = list(blurred.crop(box).getdata())
        self.assertLess(statistics.pstdev(values), 20)


class BlurredCopyIsMadeOnUploadAndEffectChangeTests(PhotoEffectTestCase):

    def setUp(self):
        super().setUp()
        self.as_owner()
        self.draft = make_listing(self.shop, status=Listing.Status.DRAFT)

    def upload_photo(self):
        return self.client.post(
            reverse("upload_listing_photo", kwargs={"pk": self.draft.pk}),
            {"photo": photo_upload()},
            format="multipart",
        )

    def patch(self, **fields):
        return self.client.patch(self.detail_url(self.draft), fields, format="json")

    def test_uploading_a_photo_for_a_magic_offer_makes_the_blurred_copy(self):
        self.assertEqual(self.patch(photo_effect=MAGIC).status_code, 200)

        self.assertEqual(self.upload_photo().status_code, 200)

        self.draft.refresh_from_db()
        self.assertTrue(self.draft.photo_blurred)
        self.assertTrue(self.draft.photo_blurred.storage.exists(self.draft.photo_blurred.name))

    def test_changing_the_effect_makes_or_removes_the_blurred_copy(self):
        self.upload_photo()
        self.draft.refresh_from_db()
        self.assertFalse(self.draft.photo_blurred)

        self.assertEqual(self.patch(photo_effect=BLURRED).status_code, 200)
        self.draft.refresh_from_db()
        blurred = self.draft.photo_blurred.name
        self.assertTrue(blurred)

        # Blurred to magic keeps the same copy.
        self.patch(photo_effect=MAGIC)
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.photo_blurred.name, blurred)

        # Back to normal: the copy is deleted.
        self.patch(photo_effect=NORMAL)
        self.draft.refresh_from_db()
        self.assertFalse(self.draft.photo_blurred)
        self.assertFalse(self.draft.photo.storage.exists(blurred))

    def test_a_new_photo_gets_a_new_blurred_copy(self):
        self.patch(photo_effect=MAGIC)
        self.upload_photo()
        self.draft.refresh_from_db()
        first = self.draft.photo_blurred.name

        self.upload_photo()

        self.draft.refresh_from_db()
        self.assertNotEqual(self.draft.photo_blurred.name, first)
        self.assertFalse(self.draft.photo.storage.exists(first))

    def test_an_unknown_effect_is_refused(self):
        response = self.patch(photo_effect="SHARP")

        self.assertEqual(response.status_code, 400)
        self.assertIn("photo_effect", response.json())

    def test_a_new_offer_is_normal_unless_the_owner_chooses(self):
        response = self.client.post(self.list_url, {"title": "Soup"}, format="json")

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["photo_effect"], NORMAL)

    def test_deleting_the_draft_deletes_its_blurred_copy(self):
        self.patch(photo_effect=BLURRED)
        self.upload_photo()
        self.draft.refresh_from_db()
        blurred = self.draft.photo_blurred.name

        response = self.client.post(reverse("listing_delete_draft", kwargs={"pk": self.draft.pk}))

        self.assertEqual(response.status_code, 204)
        self.assertFalse(self.draft.photo.storage.exists(blurred))


class OriginalIsNotReachableThroughMediaTests(PhotoEffectTestCase):

    def fetch(self, path):
        request = RequestFactory().get("/media/" + path)
        try:
            return guarded_media(request, path).status_code
        except Http404:
            return 404

    def test_the_original_of_a_blurred_or_magic_offer_is_not_served(self):
        for effect in (BLURRED, MAGIC):
            with self.subTest(effect=effect):
                listing = self.offer(effect)
                name = listing.photo.name
                folder, file = name.split("/")
                for spelling in (
                    name,
                    name.upper(),
                    f"{folder}\\{file}",
                    f"{folder}/./{file}",
                    f"{folder}//{file}",
                    f"x/../{name}",
                    f"{folder}./{file}",
                    f"{name}.",
                ):
                    self.assertEqual(self.fetch(spelling), 404, spelling)

                # The blurred copy is served.
                self.assertEqual(self.fetch(listing.photo_blurred.name), 200)

    def test_a_normal_offers_photo_is_served(self):
        listing = self.offer(NORMAL)

        self.assertEqual(self.fetch(listing.photo.name), 200)

    def test_files_outside_the_upload_folders_are_not_served(self):
        self.assertEqual(self.fetch("../config/settings.py"), 404)
        self.assertEqual(self.fetch("listing_photos/"), 404)


class MagicPeekTests(PhotoEffectTestCase):

    def setUp(self):
        super().setUp()
        self.listing = self.offer(MAGIC)

    def test_a_signed_in_customer_peeks_once_and_the_point_is_saved(self):
        self.as_customer()

        response = self.tap(self.listing, x=25, y=50)

        self.assertEqual(response.status_code, 201)
        peek = PhotoPeek.objects.get()
        self.assertEqual(
            (peek.listing_id, peek.customer_user_id, float(peek.x), float(peek.y)),
            (self.listing.id, CUSTOMER_ID, 25.0, 50.0),
        )
        body = response.json()
        self.assertEqual((body["listing_id"], body["x"], body["y"]), (self.listing.id, 25.0, 50.0))
        self.assertGreater(body["size"], 0)
        self.assertLess(body["size"], 100)

    def test_the_peek_is_a_circle_of_the_original_transparent_outside(self):
        self.as_customer()

        # 25% across is the middle of the red half; 75% is the blue half.
        red = decode_data_uri(self.tap(self.listing, x=25, y=50).json()["image"])
        self.as_customer(OTHER_CUSTOMER_ID)
        blue = decode_data_uri(self.tap(self.listing, x=75, y=50).json()["image"])

        for image, colour in ((red, RED), (blue, BLUE)):
            width, height = image.size
            self.assertEqual(width, height)
            self.assertEqual(image.getpixel((width // 2, height // 2)), (*colour, 255))
            # Corners are outside the circle.
            for corner in ((0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)):
                self.assertEqual(image.getpixel(corner)[3], 0)

    def test_the_peek_of_the_centre_shows_the_sharp_detail(self):
        self.as_customer()

        image = decode_data_uri(self.tap(self.listing, x=50, y=50).json()["image"]).convert("L")
        width = image.size[0]
        middle = image.crop((width // 2 - 8, width // 2 - 8, width // 2 + 8, width // 2 + 8))

        # The checkerboard is still black and white: the original, not the blur.
        self.assertGreater(statistics.pstdev(middle.getdata()), 60)

    def test_a_second_tap_is_refused_and_returns_the_first_peek(self):
        self.as_customer()
        first = self.tap(self.listing, x=25, y=50).json()

        second = self.tap(self.listing, x=80, y=10)

        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.json()["code"], "already_peeked")
        self.assertEqual(second.json()["peek"], first)
        self.assertEqual(PhotoPeek.objects.count(), 1)
        self.assertEqual(float(PhotoPeek.objects.get().x), 25.0)

    def test_the_database_allows_one_peek_per_customer_per_offer(self):
        self.as_customer()
        self.tap(self.listing)

        with self.assertRaises(IntegrityError), transaction.atomic():
            PhotoPeek.objects.create(
                listing=self.listing, customer_user_id=CUSTOMER_ID, x=1, y=1, size=10, cutout=b"x",
            )

    def test_reloading_returns_the_same_spot(self):
        self.as_customer()
        first = self.tap(self.listing, x=33.33, y=66.67).json()

        again = self.client.get(self.peek_url(self.listing))
        mine = self.client.get(reverse("my_peeks"), {"listings": f"{self.listing.pk},999"})

        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.json(), first)
        self.assertEqual((first["x"], first["y"]), (33.33, 66.67))
        self.assertEqual(mine.status_code, 200)
        self.assertEqual(mine.json(), [first])

    def test_before_tapping_there_is_no_saved_peek(self):
        self.as_customer()

        self.assertEqual(self.client.get(self.peek_url(self.listing)).status_code, 404)
        self.assertEqual(self.client.get(reverse("my_peeks"), {"listings": str(self.listing.pk)}).json(), [])

    def test_each_customer_gets_their_own_peek(self):
        self.as_customer()
        self.tap(self.listing, x=10, y=10)
        self.as_customer(OTHER_CUSTOMER_ID)

        self.assertEqual(self.client.get(self.peek_url(self.listing)).status_code, 404)
        self.assertEqual(self.tap(self.listing, x=90, y=90).status_code, 201)
        self.assertEqual(PhotoPeek.objects.count(), 2)

    def test_not_signed_in_cannot_peek(self):
        self.anonymous()

        self.assertEqual(self.tap(self.listing).status_code, 401)
        self.assertEqual(self.client.get(self.peek_url(self.listing)).status_code, 401)
        self.assertEqual(self.client.get(reverse("my_peeks")).status_code, 401)
        self.assertFalse(PhotoPeek.objects.exists())

    def test_restaurants_cannot_peek(self):
        self.as_owner()

        self.assertEqual(self.tap(self.listing).status_code, 403)
        self.assertFalse(PhotoPeek.objects.exists())

    def test_only_magic_photos_can_be_peeked_at(self):
        self.as_customer()
        for effect in (NORMAL, BLURRED):
            with self.subTest(effect=effect):
                listing = self.offer(effect)
                response = self.tap(listing)
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.json()["code"], "no_magic")
        self.assertFalse(PhotoPeek.objects.exists())

    def test_offers_customers_cannot_see_cannot_be_peeked_at(self):
        self.as_customer()
        draft = self.offer(MAGIC, status=Listing.Status.DRAFT)

        self.assertEqual(self.tap(draft).status_code, 404)

    def test_the_point_must_be_inside_the_photo(self):
        self.as_customer()
        for body in ({"x": 101, "y": 5}, {"x": -1, "y": 5}, {"x": "NaN", "y": 5}, {"x": True, "y": 5},
                     {"x": 5}, {"x": 5, "y": 5, "customer_user_id": 1}):
            with self.subTest(body=body):
                response = self.client.post(self.peek_url(self.listing), json.dumps(body),
                                            content_type="application/json")
                self.assertEqual(response.status_code, 400)
        self.assertFalse(PhotoPeek.objects.exists())

    def test_a_tap_on_the_edge_is_cut_off_at_the_edge(self):
        self.as_customer()

        response = self.tap(self.listing, x=0, y=100)

        self.assertEqual(response.status_code, 201)
        image = decode_data_uri(response.json()["image"])
        width, height = image.size
        # Top-right quarter of the circle is inside the photo; bottom-left is past its corner.
        self.assertEqual(image.getpixel((width * 3 // 4, height // 4))[3], 255)
        self.assertEqual(image.getpixel((width // 4, height * 3 // 4))[3], 0)


class OwnerSeesTheOriginalTests(PhotoEffectTestCase):

    def test_the_owners_list_has_the_real_photo_and_the_effect(self):
        listing = self.offer(MAGIC)
        self.as_owner()

        mine = self.client.get(reverse("my_listings")).json()

        offer = next(item for item in mine if item["id"] == listing.id)
        self.assertEqual(offer["photo_display"], listing.photo.url)
        self.assertEqual(offer["photo_effect"], MAGIC)

    def test_the_owner_downloads_the_original_whatever_the_effect(self):
        for effect in (NORMAL, BLURRED, MAGIC):
            with self.subTest(effect=effect):
                listing = self.offer(effect)
                self.as_owner()

                response = self.client.get(reverse("listing_original_photo", kwargs={"pk": listing.pk}))

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response["Content-Type"], "image/png")
                with listing.photo.open("rb") as file:
                    self.assertEqual(b"".join(response.streaming_content), file.read())

    def test_nobody_else_gets_the_original(self):
        listing = self.offer(MAGIC)
        url = reverse("listing_original_photo", kwargs={"pk": listing.pk})

        self.as_owner(user_id=OPERATOR_ID + 1)  # another restaurant
        self.assertEqual(self.client.get(url).status_code, 404)
        self.as_customer()
        self.assertEqual(self.client.get(url).status_code, 403)
        self.anonymous()
        self.assertEqual(self.client.get(url).status_code, 401)
