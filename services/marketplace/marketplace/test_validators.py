
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase
from django.utils import timezone

from .validators import validate_listing_data


class ListingValidationTests(SimpleTestCase):

    def valid_data(self):
        start = timezone.now() + timedelta(hours=2)
        end = start + timedelta(hours=2)

        return {
            "original_price": Decimal("10.00"),
            "discounted_price": Decimal("5.00"),
            "stock": 10,
            "pickup_start": start,
            "pickup_end": end,
            "publishing": True,
        }

    def test_valid_listing(self):
        validate_listing_data(**self.valid_data())

    def test_discount_higher_than_original(self):
        data = self.valid_data()
        data["discounted_price"] = Decimal("15.00")

        with self.assertRaises(ValidationError):
            validate_listing_data(**data)

    def test_zero_stock_cannot_be_published(self):
        data = self.valid_data()
        data["stock"] = 0

        with self.assertRaises(ValidationError):
            validate_listing_data(**data)

    def test_invalid_pickup_window(self):
        data = self.valid_data()
        data["pickup_end"] = data["pickup_start"] - timedelta(hours=1)

        with self.assertRaises(ValidationError):
            validate_listing_data(**data)

    def test_incomplete_draft_allowed(self):
        data = {
            "original_price": None,
            "discounted_price": None,
            "stock": 0,
            "pickup_start": None,
            "pickup_end": None,
            "publishing": False,
        }

        validate_listing_data(**data)
