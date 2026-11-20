"""LP-84: a restaurant limits how many of an offer one customer can reserve.

Marketplace stores the limit on the offer (Listing.max_per_customer, empty =
no limit), checks it when a draft is saved or published, and shows it in the
public offer API. Reservations enforces it when an order is placed.
"""

from datetime import timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .forms import ListingForm
from .listing_service import validate_listing_for_publication
from .models import Listing
from .test_lp14_permissions import published_listing
from .test_lp14_validation import ValidationTestCase


class SavingALimitTests(ValidationTestCase):

    def test_a_draft_can_be_created_with_a_limit(self):
        response = self.create(max_per_customer=2)

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["max_per_customer"], 2)
        self.assertEqual(Listing.objects.get(pk=response.data["id"]).max_per_customer, 2)

    def test_no_limit_is_the_default(self):
        response = self.create()

        self.assertEqual(response.status_code, 201)
        self.assertIsNone(response.data["max_per_customer"])

    def test_the_limit_must_be_a_whole_number_of_one_or_more(self):
        for value in (0, -1, 1.5, "two"):
            with self.subTest(value=value):
                self.assert_create_rejected("max_per_customer", max_per_customer=value)

    def test_a_draft_limit_can_be_changed_and_removed(self):
        self.assertEqual(self.edit(max_per_customer=3).data["max_per_customer"], 3)
        self.assertEqual(self.edit(max_per_customer=1).data["max_per_customer"], 1)

        response = self.edit(max_per_customer=None)

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.data["max_per_customer"])
        self.draft.refresh_from_db()
        self.assertIsNone(self.draft.max_per_customer)

    def test_a_bad_edit_keeps_the_saved_limit(self):
        self.edit(max_per_customer=3)

        response = self.edit(max_per_customer=0)

        self.assertEqual(response.status_code, 400)
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.max_per_customer, 3)

    def test_one_offer_for_several_branches_gives_each_the_same_limit(self):
        from .branch_service import add_branch
        add_branch(self.shop_a, name="Verdun", address="Verdun Street 2", area="Verdun")

        branches = [branch.id for branch in self.shop_a.branches.filter(removed_at__isnull=True)]
        self.assertGreater(len(branches), 1)
        response = self.create(
            max_per_customer=2,
            branches=[{"branch": branch, "total_quantity": 4} for branch in branches],
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual({item["max_per_customer"] for item in response.data["listings"]}, {2})


class PublishingTests(TestCase):

    def draft(self, **overrides):
        start = timezone.now() + timedelta(hours=2)
        values = dict(
            shop_id=1, title="Box", description="d", category="Bakery", pickup_address="Main street 1",
            original_price=Decimal("10.00"), discounted_price=Decimal("5.00"), total_quantity=5,
            pickup_start=start, pickup_end=start + timedelta(hours=2),
        )
        values.update(overrides)
        return Listing(**values)

    def test_a_valid_limit_does_not_stop_publishing(self):
        validate_listing_for_publication(self.draft(max_per_customer=2))
        validate_listing_for_publication(self.draft(max_per_customer=None))

    def test_the_database_refuses_a_limit_of_zero(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.draft(max_per_customer=0).save()


class ListingFormTests(TestCase):

    def data(self, **overrides):
        start = timezone.now() + timedelta(hours=2)
        return {
            "kind": "ITEM", "title": "Box", "description": "d", "category": "Bakery",
            "original_price": "10.00", "discounted_price": "5.00", "total_quantity": "5",
            "pickup_address": "Main street 1", "confirm_pickup_address": "on",
            "pickup_start": start.strftime("%Y-%m-%dT%H:%M"),
            "pickup_end": (start + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M"),
            **overrides,
        }

    def test_the_form_accepts_a_limit_or_none(self):
        self.assertTrue(ListingForm(data=self.data(max_per_customer="2"), publishing=True).is_valid())
        self.assertTrue(ListingForm(data=self.data(), publishing=True).is_valid())

    def test_the_form_refuses_a_limit_of_zero(self):
        form = ListingForm(data=self.data(max_per_customer="0"), publishing=True)

        self.assertFalse(form.is_valid())
        self.assertIn("max_per_customer", form.errors)


class PublicOfferTests(TestCase):

    def test_the_public_offer_shows_the_limit(self):
        from .test_lp14_permissions import approved_shop
        shop = approved_shop(555, "Limit Bakery")
        limited = published_listing(shop, max_per_customer=2)
        unlimited = published_listing(shop)

        limited_offer = self.client.get(reverse("listing_detail", args=[limited.pk])).json()
        unlimited_offer = self.client.get(reverse("listing_detail", args=[unlimited.pk])).json()

        self.assertEqual(limited_offer["max_per_customer"], 2)
        self.assertIsNone(unlimited_offer["max_per_customer"])

    def test_the_public_list_shows_the_limit(self):
        from .test_lp14_permissions import approved_shop
        shop = approved_shop(556, "Limit Bakery 2")
        published_listing(shop, max_per_customer=3)

        offers = self.client.get(reverse("listings")).json()
        offers = offers["results"] if isinstance(offers, dict) and "results" in offers else offers

        self.assertEqual([offer["max_per_customer"] for offer in offers], [3])
