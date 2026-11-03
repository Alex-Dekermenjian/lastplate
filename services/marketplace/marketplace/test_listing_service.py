
from django.test import TestCase

from .listing_service import (
    save_listing_draft,
    validate_listing_for_publication,
    publish_listing,
)
from .models import Listing
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.utils import timezone
from decimal import Decimal
from django.core.exceptions import PermissionDenied


class ListingServiceTests(TestCase):

    def test_save_draft(self):
        listing = save_listing_draft(
            {
                "title": "Croissant Box",
                "description": "Fresh croissants",
                "category": "Bakery",
                "original_price": "10.00",
                "discounted_price": "5.00",
                "total_quantity": 10,
            },
            shop_id=1,
        )

        self.assertIsNotNone(listing.pk)
        self.assertEqual(listing.title, "Croissant Box")
        self.assertEqual(listing.status, Listing.Status.DRAFT)
        self.assertEqual(listing.shop_id, 1)

        self.assertTrue(
            Listing.objects.filter(pk=listing.pk).exists()
        )

class ListingPublicationTests(TestCase):

    def create_valid_listing(self):
        start = timezone.now() + timedelta(hours=2)

        return Listing.objects.create(
            shop_id=1,
            title="Croissant Box",
            description="Fresh croissants",
            category="Bakery",
            original_price=Decimal("10.00"),
            discounted_price=Decimal("5.00"),
            total_quantity=10,
            pickup_address="Beirut",
            pickup_start=start,
            pickup_end=start + timedelta(hours=2),
            status=Listing.Status.DRAFT,
        )

    def test_valid_listing_can_pass_validation(self):
        listing = self.create_valid_listing()
        validate_listing_for_publication(listing)

    def test_missing_title_is_rejected(self):
        listing = self.create_valid_listing()
        listing.title = ""

        with self.assertRaises(ValidationError):
            validate_listing_for_publication(listing)

    def test_zero_stock_is_rejected(self):
        listing = self.create_valid_listing()
        listing.total_quantity = 0

        with self.assertRaises(ValidationError):
            validate_listing_for_publication(listing)

    def test_past_pickup_end_is_rejected(self):
        listing = self.create_valid_listing()
        listing.pickup_end = timezone.now() - timedelta(hours=1)

        with self.assertRaises(ValidationError):
            validate_listing_for_publication(listing)

    def test_invalid_discount_is_rejected(self):
        listing = self.create_valid_listing()
        listing.discounted_price = Decimal("15.00")

        with self.assertRaises(ValidationError):
            validate_listing_for_publication(listing)
    
    def test_approved_shop_can_publish(self):
        listing = self.create_valid_listing()

        published = publish_listing(
            listing_id=listing.id,
            shop_id=1,
            shop_approved=True,
            profile_complete=True,
        )

        published.refresh_from_db()
        self.assertEqual(
            published.status,
            Listing.Status.PUBLISHED,
        )

    def test_pending_shop_cannot_publish(self):
        listing = self.create_valid_listing()

        with self.assertRaises(PermissionDenied):
            publish_listing(
                listing_id=listing.id,
                shop_id=1,
                shop_approved=False,
                profile_complete=True,
            )

        listing.refresh_from_db()
        self.assertEqual(listing.status, Listing.Status.DRAFT)

    def test_other_shop_cannot_publish(self):
        listing = self.create_valid_listing()

        with self.assertRaises(PermissionDenied):
            publish_listing(
                listing_id=listing.id,
                shop_id=2,
                shop_approved=True,
                profile_complete=True,
            )

        listing.refresh_from_db()
        self.assertEqual(listing.status, Listing.Status.DRAFT)

    def test_incomplete_profile_cannot_publish(self):
        listing = self.create_valid_listing()

        with self.assertRaises(PermissionDenied):
            publish_listing(
                listing_id=listing.id,
                shop_id=1,
                shop_approved=True,
                profile_complete=False,
            )

        listing.refresh_from_db()
        self.assertEqual(listing.status, Listing.Status.DRAFT)



