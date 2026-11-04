
from django.test import SimpleTestCase
from .serializers import ListingSerializer
from .models import Listing

class ListingSerializerTests(SimpleTestCase):

    def test_valid_draft(self):
        serializer = ListingSerializer(data={
            "title": "Croissant Box",
            "original_price": "10.00",
            "discounted_price": "5.00",
            "total_quantity": 10,
        })

        self.assertTrue(
            serializer.is_valid(),
            serializer.errors,
        )

    def test_invalid_discount(self):
        serializer = ListingSerializer(data={
            "title": "Croissant Box",
            "original_price": "10.00",
            "discounted_price": "15.00",
            "total_quantity": 10,
        })

        self.assertFalse(serializer.is_valid())
        self.assertIn(
            "discounted_price",
            serializer.errors,
        )

    def test_negative_stock(self):
        serializer = ListingSerializer(data={
            "title": "Croissant Box",
            "total_quantity": -5,
        })

        self.assertFalse(serializer.is_valid())

    def test_cannot_set_published_status(self):
        serializer = ListingSerializer(data={
            "title": "Croissant Box",
            "status": "PUBLISHED",
        })

        self.assertTrue(
            serializer.is_valid(),
            serializer.errors,
        )
        self.assertNotIn(
            "status",
            serializer.validated_data,
        )

class ListingDisplayTests(SimpleTestCase):
    
    def test_missing_photo_returns_null(self):
        listing = Listing(
            title="Croissant Box",
            photo=None,
        )

        data = ListingSerializer(listing).data

        self.assertIsNone(data["photo_display"])


    def test_missing_ingredients_show_unknown(self):
        listing = Listing(
            title="Croissant Box",
            ingredients="",
            allergens="",
        )

        data = ListingSerializer(listing).data

        self.assertEqual(
            data["ingredients_display"],
            "Unknown",
        )

    def test_missing_allergens_show_unknown(self):
        listing = Listing(
            title="Croissant Box",
            ingredients="Flour, Butter",
            allergens="",
        )

        data = ListingSerializer(listing).data

        self.assertEqual(
            data["allergens_display"],
            "Unknown",
        )

    def test_provided_information_is_displayed(self):
        listing = Listing(
            title="Croissant Box",
            ingredients="Flour, Butter",
            allergens="Milk, Wheat",
        )

        data = ListingSerializer(listing).data

        self.assertEqual(
            data["ingredients_display"],
            "Flour, Butter",
        )
        self.assertEqual(
            data["allergens_display"],
            "Milk, Wheat",
        )
