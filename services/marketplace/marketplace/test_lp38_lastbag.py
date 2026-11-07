"""LP-38: A restaurant publishes a LastBag (surprise bag).

A LastBag uses the existing fields: its worth is original_price, its price
discounted_price and "May contain" is allergens. It adds Listing.kind and
Listing.typical_contents.
"""

from datetime import timedelta
from decimal import Decimal
from io import BytesIO

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, connection
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from PIL import Image
from rest_framework.test import APIClient

from .authentication import TokenUser
from .forms import ListingForm
from .listing_service import validate_listing_for_publication
from .models import Listing, Shop
from .test_browse import make_listing, make_shop
from .test_migration_repair import LATEST, migrate_to

OPERATOR = 987654


def bag_fields(**overrides):
    start = timezone.now() + timedelta(hours=2)
    return {
        "kind": Listing.Kind.LASTBAG,
        "title": "Bakery surprise bag",
        "typical_contents": "Croissants, man'oushe, a loaf of bread",
        "allergens": "Gluten, milk, sesame",
        "original_price": Decimal("15.00"),
        "discounted_price": Decimal("5.00"),
        "total_quantity": 3,
        "pickup_address": "12 Bliss Street, Beirut",
        "pickup_start": start,
        "pickup_end": start + timedelta(hours=1),
        **overrides,
    }


def bag_payload(**overrides):
    start = timezone.now() + timedelta(hours=2)
    return {
        "kind": "LASTBAG",
        "typical_contents": "Croissants, man'oushe, a loaf of bread",
        "allergens": "Gluten, milk, sesame",
        "original_price": "15.00",
        "discounted_price": "5.00",
        "total_quantity": 3,
        "pickup_address": "12 Bliss Street, Beirut",
        "pickup_start": start.isoformat(),
        "pickup_end": (start + timedelta(hours=1)).isoformat(),
        **overrides,
    }


def png_upload(name="bag.png"):
    buffer = BytesIO()
    Image.new("RGB", (20, 20), "orange").save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


class LastBagPublicationRulesTests(TestCase):
    """The checks run when a draft is published."""

    def bag(self, **overrides):
        return Listing.objects.create(shop_id=1, **bag_fields(**overrides))

    def errors(self, listing):
        with self.assertRaises(ValidationError) as caught:
            validate_listing_for_publication(listing)
        return caught.exception.message_dict

    def test_complete_lastbag_passes_without_ingredients_description_or_category(self):
        listing = self.bag()

        self.assertEqual(listing.ingredients, "")
        self.assertEqual(listing.description, "")
        self.assertEqual(listing.category, "")
        validate_listing_for_publication(listing)

    def test_missing_typical_contents_is_refused(self):
        errors = self.errors(self.bag(typical_contents="   "))

        self.assertEqual(errors, {"typical_contents": ["Describe what's usually inside the bag."]})

    def test_missing_allergens_is_refused(self):
        errors = self.errors(self.bag(allergens=""))

        self.assertEqual(list(errors), ["allergens"])

    def test_price_not_below_worth_is_refused(self):
        errors = self.errors(self.bag(discounted_price=Decimal("15.00")))

        self.assertEqual(
            errors,
            {"discounted_price": ["Your price must be less than the bag's worth."]},
        )

    def test_quantity_and_pickup_window_are_checked_as_for_items(self):
        listing = self.bag(total_quantity=0, pickup_end=timezone.now() - timedelta(hours=1))

        errors = self.errors(listing)

        self.assertIn("total_quantity", errors)
        self.assertIn("pickup_end", errors)

    def test_item_rules_are_unchanged(self):
        start = timezone.now() + timedelta(hours=2)
        item = Listing.objects.create(
            shop_id=1,
            title="6 butter croissants",
            category="Bakery",
            original_price=Decimal("9.00"),
            discounted_price=Decimal("4.00"),
            total_quantity=4,
            pickup_address="12 Bliss Street, Beirut",
            pickup_start=start,
            pickup_end=start + timedelta(hours=1),
        )

        self.assertEqual(item.kind, Listing.Kind.ITEM)
        # An item still needs a description; it never needs contents or allergens.
        self.assertEqual(self.errors(item), {"description": ["This field is required."]})

        item.description = "Fresh this morning"
        validate_listing_for_publication(item)

        item.discounted_price = Decimal("9.00")
        self.assertEqual(
            self.errors(item),
            {"discounted_price": ["Discounted price must be less than original price."]},
        )


class LastBagFormTests(TestCase):
    """The Marketplace ListingForm applies the same rules."""

    def form(self, data, **kwargs):
        return ListingForm(data=data, publishing=True, **kwargs)

    def form_data(self, **overrides):
        start = timezone.localtime() + timedelta(hours=2)
        return {
            "kind": "LASTBAG",
            "typical_contents": "Croissants and bread",
            "allergens": "Gluten",
            "original_price": "15.00",
            "discounted_price": "5.00",
            "total_quantity": "3",
            "pickup_address": "12 Bliss Street, Beirut",
            "pickup_start": start.strftime("%Y-%m-%dT%H:%M"),
            "pickup_end": (start + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"),
            "confirm_pickup_address": "on",
            **overrides,
        }

    def test_complete_lastbag_is_valid_without_title_or_ingredients(self):
        form = self.form(self.form_data())

        self.assertTrue(form.is_valid(), form.errors)

    def test_lastbag_needs_contents_and_allergens(self):
        form = self.form(self.form_data(typical_contents="", allergens=""))

        self.assertFalse(form.is_valid())
        self.assertEqual(set(form.errors), {"typical_contents", "allergens"})

    def test_lastbag_price_must_be_below_worth(self):
        form = self.form(self.form_data(discounted_price="20.00"))

        self.assertFalse(form.is_valid())
        self.assertEqual(
            form.errors["discounted_price"],
            ["Your price must be less than the bag's worth."],
        )

    def test_item_form_is_unchanged(self):
        form = self.form(self.form_data(kind="ITEM"))

        self.assertFalse(form.is_valid())
        self.assertEqual(set(form.errors), {"title", "description", "category"})

    def test_editing_a_draft_keeps_its_kind(self):
        listing = Listing.objects.create(shop_id=1, **bag_fields())
        form = ListingForm(
            data=self.form_data(kind="ITEM"),
            instance=listing,
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().kind, Listing.Kind.LASTBAG)


class LastBagAPITests(TestCase):

    def setUp(self):
        self.client = APIClient()
        self.shop = make_shop(operator_user_id=OPERATOR, category=Shop.Category.BAKERY)
        self.client.force_authenticate(
            user=TokenUser(id=OPERATOR, role="SHOP_OPERATOR", is_staff=False)
        )

    def create(self, **overrides):
        return self.client.post(reverse("listings"), bag_payload(**overrides), format="json")

    def edit(self, listing, data):
        return self.client.patch(
            reverse("listing_detail", kwargs={"pk": listing.pk}), data, format="json"
        )

    def publish(self, listing):
        return self.client.post(reverse("publish_listing", kwargs={"pk": listing.pk}))

    def test_create_accepts_and_returns_kind_and_typical_contents(self):
        response = self.create()

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["kind"], "LASTBAG")
        self.assertEqual(response.data["typical_contents"], "Croissants, man'oushe, a loaf of bread")
        self.assertEqual(response.data["original_price"], "15.00")
        self.assertEqual(response.data["discounted_price"], "5.00")
        listing = Listing.objects.get()
        self.assertEqual(listing.kind, Listing.Kind.LASTBAG)

    def test_lastbag_without_title_is_named_after_the_restaurant_category(self):
        self.assertEqual(self.create().data["title"], "Bakery surprise bag")

        self.shop.category = Shop.Category.OTHER
        self.shop.save()
        self.assertEqual(self.create().data["title"], "Surprise bag")

        self.assertEqual(self.create(title="Evening bag").data["title"], "Evening bag")

    def test_item_is_the_default_kind(self):
        response = self.client.post(
            reverse("listings"), {"title": "6 butter croissants"}, format="json"
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["kind"], "ITEM")
        self.assertEqual(response.data["typical_contents"], "")

    def test_unknown_kind_is_refused(self):
        response = self.create(kind="MYSTERY")

        self.assertEqual(response.status_code, 400)
        self.assertIn("kind", response.data)
        self.assertEqual(Listing.objects.count(), 0)

    def test_unknown_and_read_only_fields_are_still_refused(self):
        for field, value in (("is_lastbag", True), ("free_quantity", 9), ("worth", "15.00")):
            with self.subTest(field=field):
                response = self.create(**{field: value})

                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.data[field], ["This field is not allowed."])

        self.assertEqual(Listing.objects.count(), 0)

    def test_price_not_below_worth_is_refused_on_save(self):
        response = self.create(discounted_price="15.00")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data["discounted_price"],
            ["Your price must be less than the bag's worth."],
        )

    def test_editing_keeps_the_kind(self):
        listing = Listing.objects.create(shop_id=self.shop.id, **bag_fields())

        refused = self.edit(listing, {"kind": "ITEM"})
        self.assertEqual(refused.status_code, 400)
        self.assertIn("kind", refused.data)

        same = self.edit(listing, {"kind": "LASTBAG", "typical_contents": "Bread and cakes"})
        self.assertEqual(same.status_code, 200, same.data)
        listing.refresh_from_db()
        self.assertEqual(listing.kind, Listing.Kind.LASTBAG)
        self.assertEqual(listing.typical_contents, "Bread and cakes")

    def test_item_cannot_become_a_lastbag(self):
        item = Listing.objects.create(shop_id=self.shop.id, title="6 croissants")

        self.assertEqual(self.edit(item, {"kind": "LASTBAG"}).status_code, 400)
        item.refresh_from_db()
        self.assertEqual(item.kind, Listing.Kind.ITEM)

    def test_publish_refuses_lastbag_without_contents_or_allergens(self):
        listing = Listing.objects.create(
            shop_id=self.shop.id, **bag_fields(typical_contents="", allergens="")
        )

        response = self.publish(listing)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(set(response.data["fields"]), {"typical_contents", "allergens"})
        listing.refresh_from_db()
        self.assertEqual(listing.status, Listing.Status.DRAFT)

    def test_lastbag_publishes_and_its_stock_can_change(self):
        draft_id = self.create().data["id"]
        listing = Listing.objects.get(pk=draft_id)

        published = self.publish(listing)
        self.assertEqual(published.status_code, 200, published.data)
        self.assertEqual(published.data["status"], "PUBLISHED")
        self.assertEqual(published.data["kind"], "LASTBAG")

        adjusted = self.client.post(
            reverse("listing_stock_adjustments", kwargs={"pk": draft_id}),
            {"quantity_change": 2, "reason": "Two more bags packed"},
            format="json",
        )
        self.assertEqual(adjusted.status_code, 201, adjusted.data)
        self.assertEqual(adjusted.data["listing"]["total_quantity"], 5)
        self.assertEqual(adjusted.data["listing"]["kind"], "LASTBAG")

    def test_lastbag_draft_takes_a_normal_photo(self):
        listing = Listing.objects.create(shop_id=self.shop.id, **bag_fields())

        response = self.client.post(
            reverse("upload_listing_photo", kwargs={"pk": listing.pk}),
            {"photo": png_upload()},
            format="multipart",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["photo_display"])
        self.assertEqual(response.data["kind"], "LASTBAG")


class LastBagBrowsingTests(TestCase):

    def setUp(self):
        self.client = APIClient()
        self.shop = make_shop()
        self.bag = make_listing(
            self.shop,
            **bag_fields(ingredients="Secret: 2 croissants, 1 loaf"),
            status=Listing.Status.PUBLISHED,
        )
        self.item = make_listing(self.shop, title="6 butter croissants")

    def test_offer_list_shows_lastbags_with_worth_price_and_typical_contents(self):
        response = self.client.get(reverse("listings"))

        self.assertEqual(response.status_code, 200)
        offers = {offer["id"]: offer for offer in response.json()}
        bag = offers[self.bag.id]
        self.assertEqual(bag["kind"], "LASTBAG")
        self.assertEqual(bag["typical_contents"], "Croissants, man'oushe, a loaf of bread")
        self.assertEqual(bag["original_price"], "15.00")
        self.assertEqual(bag["discounted_price"], "5.00")
        self.assertEqual(bag["allergens_display"], "Gluten, milk, sesame")
        self.assertEqual(offers[self.item.id]["kind"], "ITEM")

    def test_customers_never_see_a_lastbags_exact_contents(self):
        for url in (reverse("listings"), reverse("listing_detail", kwargs={"pk": self.bag.pk})):
            with self.subTest(url=url):
                response = self.client.get(url)

                self.assertNotIn("Secret", response.content.decode())

        detail = self.client.get(reverse("listing_detail", kwargs={"pk": self.bag.pk})).json()
        self.assertEqual(detail["kind"], "LASTBAG")
        self.assertEqual(detail["ingredients_display"], "Changes daily")

    def test_items_still_show_their_ingredients(self):
        self.item.ingredients = "Flour, butter"
        self.item.save()

        detail = self.client.get(reverse("listing_detail", kwargs={"pk": self.item.pk})).json()

        self.assertEqual(detail["ingredients_display"], "Flour, butter")


class LastBagMigrationTests(TransactionTestCase):
    """0009 adds kind and typical_contents; offers made before it are items."""

    def setUp(self):
        migrate_to("0008_merge_shop_application_shop_reviewed_by_user_id")
        self.addCleanup(migrate_to, LATEST)

    def test_existing_offers_become_items(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO marketplace_listing (shop_id, title, description, category, total_quantity, "
                "pickup_address, status, terms_revision, created_at, updated_at, ingredients, allergens) "
                "VALUES (9, 'Older offer', '', '', 2, '', 'PUBLISHED', 1, now(), now(), '', '')"
            )

        migrate_to("0009_listing_kind_typical_contents")

        with connection.cursor() as cursor:
            cursor.execute("SELECT title, kind, typical_contents FROM marketplace_listing")
            self.assertEqual(cursor.fetchall(), [("Older offer", "ITEM", "")])

    def test_database_refuses_an_unknown_kind(self):
        migrate_to("0009_listing_kind_typical_contents")

        with self.assertRaises(IntegrityError), connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO marketplace_listing (shop_id, title, description, category, total_quantity, "
                "pickup_address, status, terms_revision, created_at, updated_at, ingredients, allergens, "
                "kind, typical_contents) "
                "VALUES (9, 'Bad', '', '', 0, '', 'DRAFT', 1, now(), now(), '', '', 'MYSTERY', '')"
            )
