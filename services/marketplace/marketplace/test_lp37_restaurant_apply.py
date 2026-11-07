"""LP-37: a restaurant owner sends the request to join LastPlate."""

from decimal import Decimal
from io import BytesIO

from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.urls import reverse
from PIL import Image

from .models import Shop
from .test_browse import MarketplaceTestCase, make_listing
from .test_shops import ShopAPITestCase, make_token

OWNER = 7
CATEGORIES = ["Bakery", "Café", "Restaurant", "Sushi", "Pastry", "Grocery", "Other"]


def image_bytes(image_format="JPEG", colour=(200, 120, 40)):
    buffer = BytesIO()
    Image.new("RGB", (8, 8), colour).save(buffer, format=image_format)
    return buffer.getvalue()


def upload(name="cover.jpg", image_format="JPEG", content_type="image/jpeg"):
    return SimpleUploadedFile(name, image_bytes(image_format), content_type=content_type)


def application(**overrides):
    data = {
        "name": "Cedar Bakery",
        "category": "Bakery",
        "public_contact": "+961 1 234 567",
        "address": "12 Bliss Street, Beirut",
        "area": "Hamra",
        "photo": upload(),
        "accept_conditions": "true",
    }
    data.update(overrides)
    return {key: value for key, value in data.items() if value is not None}


class ApplicationTestCase(ShopAPITestCase):
    url = reverse("shop-application")

    def send(self, data=None, user_id=OWNER, role="SHOP_OPERATOR"):
        self.as_user(make_token(user_id=user_id, role=role))
        return self.client.post(self.url, data if data is not None else application(), format="multipart")


class SendApplicationTests(ApplicationTestCase):
    def test_sending_creates_a_pending_restaurant_with_every_detail(self):
        response = self.send(application(category="Café", latitude="33.896944", longitude="35.478333",
                                         logo=upload("logo.png", "PNG", "image/png")))

        self.assertEqual(response.status_code, 201, response.data)
        shop = Shop.objects.get(operator_user_id=OWNER)
        self.assertEqual(shop.approval_status, "PENDING")
        self.assertEqual((shop.name, shop.category, shop.public_contact, shop.address, shop.area),
                         ("Cedar Bakery", "Café", "+961 1 234 567", "12 Bliss Street, Beirut", "Hamra"))
        self.assertEqual((shop.latitude, shop.longitude), (Decimal("33.896944"), Decimal("35.478333")))
        self.assertTrue(shop.photo.name.startswith("shop_photos/"))
        self.assertTrue(shop.logo.name.startswith("shop_logos/"))
        self.assertIsNotNone(shop.conditions_accepted_at)
        self.assertTrue(shop.profile_complete)

        self.assertEqual(response.data["category"], "Café")
        self.assertEqual(response.data["photo_display"], shop.photo.url)
        self.assertEqual(response.data["logo_display"], shop.logo.url)
        self.assertTrue(response.data["application_sent"])
        self.assertEqual(response.data["approval_status"], "PENDING")

    def test_location_coordinates_and_logo_are_optional(self):
        response = self.send()

        self.assertEqual(response.status_code, 201, response.data)
        shop = Shop.objects.get(operator_user_id=OWNER)
        self.assertIsNone(shop.latitude)
        self.assertFalse(shop.logo)
        self.assertIsNone(response.data["logo_display"])

    def test_every_category_is_stored_as_its_readable_word(self):
        for number, category in enumerate(CATEGORIES, start=100):
            with self.subTest(category=category):
                response = self.send(application(category=category), user_id=number)
                self.assertEqual(response.status_code, 201, response.data)
                self.assertEqual(Shop.objects.get(operator_user_id=number).category, category)

    def test_only_shop_operators_can_send(self):
        self.assertEqual(self.send(role="CUSTOMER").status_code, 403)
        self.client.credentials()
        self.assertEqual(self.client.post(self.url, application(), format="multipart").status_code, 401)
        self.assertFalse(Shop.objects.exists())


class StepValidationTests(ApplicationTestCase):
    def assert_refused(self, data, field):
        response = self.send(data)
        self.assertEqual(response.status_code, 400)
        self.assertIn(field, response.data)
        self.assertFalse(Shop.objects.filter(operator_user_id=OWNER).exists())

    def test_step_1_restaurant_info(self):
        for field, value in (
            ("name", None), ("name", ""), ("name", "x"), ("name", "x" * 151),
            ("category", None), ("category", "bakery"), ("category", "Pizza"),
            ("public_contact", None), ("public_contact", "call me"), ("public_contact", "123"),
        ):
            with self.subTest(field=field, value=value):
                self.assert_refused(application(**{field: value}), field)

    def test_step_2_location(self):
        for field, value, error_field in (
            ("address", None, "address"), ("address", "abc", "address"),
            ("area", None, "area"),
            ("latitude", "91", "latitude"), ("longitude", "-181", "longitude"),
            ("latitude", "north", "latitude"),
        ):
            with self.subTest(field=field, value=value):
                self.assert_refused(application(**{field: value}), error_field)

    def test_step_2_coordinates_come_in_pairs(self):
        self.assert_refused(application(latitude="33.9"), "longitude")

    def test_step_3_cover_photo_is_required(self):
        self.assert_refused(application(photo=None), "photo")

    def test_step_3_conditions_must_be_accepted(self):
        for value in (None, "false"):
            with self.subTest(accept_conditions=value):
                response = self.send(application(accept_conditions=value))
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.data["accept_conditions"],
                                 ["Tick the box to accept LastPlate's conditions for restaurants."])
        self.assertFalse(Shop.objects.exists())

    def test_bad_image_types_are_refused(self):
        gif = SimpleUploadedFile("cover.gif", image_bytes("GIF"), content_type="image/gif")
        renamed_text = SimpleUploadedFile("cover.jpg", b"not really a photo", content_type="image/jpeg")
        for field in ("photo", "logo"):
            for bad in (gif, renamed_text):
                with self.subTest(field=field, file=bad.name):
                    bad.seek(0)
                    self.assert_refused(application(**{field: bad}), field)
        response = self.send(application(photo=SimpleUploadedFile("cover.jpg", image_bytes("GIF"))))
        self.assertEqual(response.data["photo"], ["Only JPEG and PNG images are allowed."])

    def test_ownership_and_approval_cannot_be_sent(self):
        for field, value in (("approval_status", "APPROVED"), ("operator_user_id", "99"),
                             ("conditions_accepted_at", "2026-01-01T00:00:00Z"), ("id", "1")):
            with self.subTest(field=field):
                response = self.send(application(**{field: value}))
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.data[field], ["This field is not allowed."])
        self.assertFalse(Shop.objects.exists())


class OneRestaurantPerOwnerTests(ApplicationTestCase):
    def test_sending_again_updates_the_same_restaurant(self):
        first = self.send()
        second = self.send(application(name="Cedar Bakery Hamra", photo=None))

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200, second.data)
        self.assertEqual(Shop.objects.filter(operator_user_id=OWNER).count(), 1)
        self.assertEqual(second.data["id"], first.data["id"])
        self.assertEqual(second.data["name"], "Cedar Bakery Hamra")
        # The photo sent the first time is kept.
        self.assertEqual(second.data["photo_display"], first.data["photo_display"])

    def test_a_shop_from_the_old_name_only_setup_is_completed_not_duplicated(self):
        old = Shop.objects.create(operator_user_id=OWNER, name="Cedar")

        response = self.send()

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["id"], old.id)
        self.assertEqual(Shop.objects.count(), 1)

    def test_a_new_cover_photo_replaces_the_old_file(self):
        self.send()
        old = Shop.objects.get().photo
        old_name, storage = old.name, old.storage

        self.send(application(photo=upload("new.png", "PNG", "image/png")))

        self.assertFalse(storage.exists(old_name))
        self.assertTrue(Shop.objects.get().photo.name.endswith(".png"))

    def test_reviewed_restaurants_cannot_be_resent(self):
        for status in ("APPROVED", "REJECTED"):
            with self.subTest(status=status):
                Shop.objects.all().delete()
                Shop.objects.create(operator_user_id=OWNER, name="Cedar", approval_status=status)
                response = self.send()
                self.assertEqual(response.status_code, 409)
                shop = Shop.objects.get()
                self.assertEqual((shop.name, shop.approval_status), ("Cedar", status))

    def test_the_database_allows_one_restaurant_per_owner(self):
        Shop.objects.create(operator_user_id=OWNER, name="First")
        with self.assertRaises(IntegrityError):
            Shop.objects.create(operator_user_id=OWNER, name="Second")

    def test_the_old_create_endpoint_returns_the_existing_restaurant(self):
        sent = self.send()
        again = self.create({"name": "Second shop"}, user_id=OWNER)

        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.data["id"], sent.data["id"])
        self.assertEqual(Shop.objects.count(), 1)


class OwnProfileTests(ApplicationTestCase):
    me_url = reverse("shop-me")

    def setUp(self):
        self.shop = Shop.objects.create(operator_user_id=OWNER, name="Cedar Bakery")
        self.other = Shop.objects.create(operator_user_id=8, name="Other Bakery", category="Sushi")

    def patch(self, data, format="json", user_id=OWNER):
        self.as_user(make_token(user_id=user_id))
        return self.client.patch(self.me_url, data, format=format)

    def test_new_and_existing_shops_default_to_other_without_photo(self):
        self.as_user(make_token(user_id=OWNER))
        data = self.client.get(self.me_url).data
        self.assertEqual((data["category"], data["photo_display"], data["logo_display"]), ("Other", None, None))
        self.assertFalse(data["application_sent"])

    def test_owner_changes_category_and_images_of_their_own_restaurant(self):
        response = self.patch({"category": "Pastry", "photo": upload(), "logo": upload("logo.png", "PNG", "image/png")},
                              format="multipart")

        self.assertEqual(response.status_code, 200, response.data)
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.category, "Pastry")
        self.assertEqual(response.data["photo_display"], self.shop.photo.url)
        self.assertEqual(response.data["logo_display"], self.shop.logo.url)
        self.other.refresh_from_db()
        self.assertEqual(self.other.category, "Sushi")
        self.assertFalse(self.other.photo)

    def test_owner_cannot_choose_which_restaurant_to_edit(self):
        response = self.patch({"category": "Pastry", "id": self.other.id, "operator_user_id": 8})

        self.assertEqual(response.status_code, 400)
        self.other.refresh_from_db()
        self.assertEqual(self.other.category, "Sushi")

    def test_profile_changes_are_validated(self):
        for data in ({"category": "Pizza"}, {"latitude": "33.9"}, {"latitude": "100", "longitude": "35"}):
            with self.subTest(data=data):
                self.assertEqual(self.patch(data).status_code, 400)
        response = self.patch({"photo": SimpleUploadedFile("x.gif", image_bytes("GIF"))}, format="multipart")
        self.assertEqual(response.status_code, 400)
        self.assertIn("photo", response.data)


class PublicRestaurantDataTests(MarketplaceTestCase):
    def test_offers_show_the_restaurant_category_photo_and_logo(self):
        self.shop.category = "Bakery"
        self.shop.photo.save("cover.jpg", ContentFile(image_bytes()), save=False)
        self.shop.save()
        make_listing(self.shop)

        shop = self.client.get(self.list_url).json()[0]["shop"]

        self.assertEqual(shop["category"], "Bakery")
        self.assertEqual(shop["photo_display"], self.shop.photo.url)
        self.assertTrue(shop["photo_display"].startswith("/media/shop_photos/"))
        self.assertIsNone(shop["logo_display"])
        # Still nothing private.
        for private in ("operator_user_id", "conditions_accepted_at", "latitude", "approval_status"):
            self.assertNotIn(private, shop)
