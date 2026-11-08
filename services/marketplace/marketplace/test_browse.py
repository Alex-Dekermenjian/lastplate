"""LP-13: Public marketplace — browsing offers and viewing offer details."""

import json
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from .authentication import TokenUser
from .models import Listing, Shop

OPERATOR_ID = 987654


def make_shop(operator_user_id=OPERATOR_ID, **overrides):
    fields = {
        "operator_user_id": operator_user_id,
        "name": "Cedar Bakery",
        "address": "12 Bliss Street, Beirut",
        "area": "Hamra",
        "public_contact": "+961 1 234 567",
        "approval_status": Shop.ApprovalStatus.APPROVED,
        **overrides,
    }
    return Shop.objects.create(**fields)


def make_listing(shop, **overrides):
    start = timezone.now() + timedelta(hours=3)
    fields = {
        "shop_id": shop.id,
        "title": "Croissant Box",
        "description": "Fresh pastries",
        "category": "Bakery",
        "original_price": "10.00",
        "discounted_price": "4.50",
        "total_quantity": 5,
        "pickup_address": "12 Bliss Street, Beirut",
        "pickup_start": start,
        "pickup_end": start + timedelta(hours=2),
        "status": Listing.Status.PUBLISHED,
        **overrides,
    }
    return Listing.objects.create(**fields)


class MarketplaceTestCase(TestCase):

    def setUp(self):
        self.client = APIClient()
        self.shop = make_shop()
        self.list_url = reverse("listings")

    def detail_url(self, listing_or_pk):
        pk = getattr(listing_or_pk, "pk", listing_or_pk)
        return reverse("listing_detail", kwargs={"pk": pk})

    def browse(self):
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, 200)
        return response

    def browsed_ids(self):
        return [item["id"] for item in self.browse().data]

    def assert_hidden(self, listing):
        """Hidden from the list, and its detail page is a 404."""
        self.assertNotIn(listing.id, self.browsed_ids())

        response = self.client.get(self.detail_url(listing))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(),
            {"code": "not_found", "detail": "This offer is not available."},
        )


# Every condition that keeps an offer out of the marketplace
# (an ended pickup is tested separately because it needs times).
HIDDEN_CASES = {
    "draft": ({}, {"status": Listing.Status.DRAFT}),
    "withdrawn": ({}, {"status": Listing.Status.WITHDRAWN}),
    "pending shop": ({"approval_status": Shop.ApprovalStatus.PENDING}, {}),
    "rejected shop": (
        {
            "approval_status": Shop.ApprovalStatus.REJECTED,
            "rejection_reason": "Not a real shop",
        },
        {},
    ),
    "sold out": ({}, {"total_quantity": 0}),
}


class BrowseListingsTests(MarketplaceTestCase):

    def test_anyone_can_browse_without_signing_in(self):
        listing = make_listing(self.shop)

        self.assertEqual(self.browsed_ids(), [listing.id])

    def test_a_stale_token_does_not_block_public_browsing(self):
        listing = make_listing(self.shop)
        self.client.credentials(HTTP_AUTHORIZATION="Bearer not-a-real-token")

        self.assertEqual(self.browsed_ids(), [listing.id])

    def test_offer_shows_what_customers_need(self):
        listing = make_listing(
            self.shop,
            ingredients="Flour, butter",
            allergens="Gluten, milk",
        )

        item = self.browse().data[0]

        self.assertEqual(item["id"], listing.id)
        self.assertEqual(item["title"], "Croissant Box")
        self.assertEqual(item["original_price"], "10.00")
        self.assertEqual(item["discounted_price"], "4.50")
        self.assertEqual(item["free_quantity"], 5)
        self.assertEqual(item["pickup_address"], "12 Bliss Street, Beirut")
        self.assertIsNotNone(item["pickup_start"])
        self.assertIsNotNone(item["pickup_end"])
        self.assertIsNone(item["photo_display"])
        self.assertEqual(item["ingredients_display"], "Flour, butter")
        self.assertEqual(item["allergens_display"], "Gluten, milk")
        self.assertEqual(
            item["shop"],
            {
                # LP-39: the shop's own id links to its restaurant page.
                "id": self.shop.id,
                "name": "Cedar Bakery",
                "area": "Hamra",
                "public_contact": "+961 1 234 567",
                # LP-37: the restaurant's category and images.
                "category": "Other",
                "photo_display": None,
                "logo_display": None,
                # LP-68: every restaurant has at least its main branch.
                "branch_count": 1,
            },
        )

    def test_missing_ingredients_and_allergens_show_unknown(self):
        make_listing(self.shop, ingredients="", allergens="   ")

        item = self.browse().data[0]

        self.assertEqual(item["ingredients_display"], "Unknown")
        self.assertEqual(item["allergens_display"], "Unknown")

    def test_list_does_not_include_private_or_operator_fields(self):
        make_listing(self.shop)

        item = self.browse().data[0]

        for field in ("status", "total_quantity", "ingredients", "allergens",
                      "photo", "shop_id", "terms_revision", "first_reserved_at"):
            self.assertNotIn(field, item)

    def test_no_operator_identity_in_response(self):
        make_listing(self.shop)

        body = json.dumps(self.browse().json())

        self.assertNotIn("operator", body)
        self.assertNotIn(str(OPERATOR_ID), body)
        self.assertNotIn("email", body)
        self.assertNotIn("@", body)

    def test_offer_whose_pickup_is_under_way_is_still_shown(self):
        now = timezone.now()
        listing = make_listing(
            self.shop,
            pickup_start=now - timedelta(minutes=30),
            pickup_end=now + timedelta(minutes=30),
        )

        self.assertEqual(self.browsed_ids(), [listing.id])

    def test_offers_ending_soonest_come_first(self):
        now = timezone.now()
        later = make_listing(
            self.shop,
            pickup_start=now + timedelta(hours=5),
            pickup_end=now + timedelta(hours=6),
        )
        sooner = make_listing(
            self.shop,
            pickup_start=now + timedelta(hours=1),
            pickup_end=now + timedelta(hours=2),
        )

        self.assertEqual(self.browsed_ids(), [sooner.id, later.id])

    def test_empty_marketplace_returns_empty_list(self):
        self.assertEqual(self.browse().data, [])

    def test_operator_can_still_create_through_the_shared_url(self):
        # The URL now also serves public GETs. Token-authenticated writes
        # must not be blocked by Django's CSRF check on the way through.
        client = APIClient(enforce_csrf_checks=True)
        client.force_authenticate(
            user=TokenUser(id=OPERATOR_ID, role="SHOP_OPERATOR", is_staff=False)
        )

        response = client.post(
            self.list_url, {"title": "Manakish"}, format="json"
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(Listing.objects.get().status, Listing.Status.DRAFT)

    def test_post_still_requires_a_shop_operator(self):
        response = self.client.post(
            self.list_url, {"title": "Sneaky"}, format="json"
        )

        self.assertIn(response.status_code, [401, 403])
        self.assertEqual(Listing.objects.count(), 0)


class HiddenOfferTests(MarketplaceTestCase):
    """Hidden offers are left out of the list and their details are 404."""

    def test_hidden_cases(self):
        for index, (name, (shop_fields, listing_fields)) in enumerate(
            HIDDEN_CASES.items()
        ):
            with self.subTest(name):
                shop = make_shop(operator_user_id=1000 + index, **shop_fields)
                listing = make_listing(shop, **listing_fields)

                self.assert_hidden(listing)

    def test_ended_offer_is_hidden(self):
        now = timezone.now()
        listing = make_listing(
            self.shop,
            pickup_start=now - timedelta(hours=3),
            pickup_end=now - timedelta(minutes=1),
        )

        self.assert_hidden(listing)

    def test_offer_is_hidden_exactly_at_pickup_end(self):
        now = timezone.now()
        listing = make_listing(
            self.shop,
            pickup_start=now - timedelta(hours=1),
            pickup_end=now,
        )

        with patch("marketplace.browse.timezone.now", return_value=now):
            self.assert_hidden(listing)

    def test_offer_sold_out_by_reservations_is_hidden(self):
        # Free stock comes from stock.free_quantity(), so units committed
        # to reservations (Sprint 2) hide the offer without other changes.
        listing = make_listing(self.shop, total_quantity=3)

        with patch("marketplace.stock.committed_quantity", return_value=3):
            self.assert_hidden(listing)

    def test_free_quantity_comes_from_stock_calculation(self):
        make_listing(self.shop, total_quantity=5)

        with patch("marketplace.stock.committed_quantity", return_value=2):
            item = self.browse().data[0]

        self.assertEqual(item["free_quantity"], 3)

    def test_shop_rejected_after_publishing_hides_its_offers(self):
        listing = make_listing(self.shop)

        self.shop.approval_status = Shop.ApprovalStatus.REJECTED
        self.shop.rejection_reason = "Complaints received"
        self.shop.save()

        self.assert_hidden(listing)

    def test_visible_offers_are_unaffected_by_hidden_ones(self):
        visible = make_listing(self.shop)
        make_listing(self.shop, status=Listing.Status.DRAFT)
        make_listing(self.shop, total_quantity=0)
        pending = make_shop(
            operator_user_id=2,
            approval_status=Shop.ApprovalStatus.PENDING,
        )
        make_listing(pending)

        self.assertEqual(self.browsed_ids(), [visible.id])


class OfferDetailTests(MarketplaceTestCase):

    def test_anyone_can_view_offer_details(self):
        listing = make_listing(self.shop)

        response = self.client.get(self.detail_url(listing))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["id"], listing.id)
        self.assertEqual(response.data["free_quantity"], 5)
        self.assertEqual(response.data["terms_revision"], listing.terms_revision)
        self.assertEqual(response.data["shop"]["name"], "Cedar Bakery")
        self.assertEqual(response.data["shop"]["area"], "Hamra")
        self.assertEqual(
            response.data["shop"]["public_contact"], "+961 1 234 567"
        )
        self.assertEqual(response.data["ingredients_display"], "Unknown")
        self.assertEqual(response.data["allergens_display"], "Unknown")

    def test_detail_has_no_operator_identity(self):
        listing = make_listing(self.shop)

        response = self.client.get(self.detail_url(listing))
        body = json.dumps(response.json())

        self.assertNotIn("operator", body)
        self.assertNotIn(str(OPERATOR_ID), body)
        self.assertNotIn("email", body)
        self.assertNotIn("@", body)

    def test_unknown_offer_is_404(self):
        response = self.client.get(self.detail_url(999999))

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "not_found")

    def test_patch_still_requires_the_owner(self):
        draft = make_listing(self.shop, status=Listing.Status.DRAFT)

        response = self.client.patch(
            self.detail_url(draft), {"title": "Changed"}, format="json"
        )

        self.assertIn(response.status_code, [401, 403])
        draft.refresh_from_db()
        self.assertEqual(draft.title, "Croissant Box")


class QueryCountTests(MarketplaceTestCase):
    """Shop details are loaded once per request, not once per offer."""

    def test_browse_query_count_does_not_grow_with_offers(self):
        make_listing(self.shop)

        with self.assertNumQueries(2):
            self.browse()

        for operator_id in (2, 3, 4):
            shop = make_shop(operator_user_id=operator_id, name=f"Shop {operator_id}")
            make_listing(shop)
            make_listing(shop)

        with self.assertNumQueries(2):
            response = self.browse()

        self.assertEqual(len(response.data), 7)

    def test_detail_uses_two_queries(self):
        listing = make_listing(self.shop)

        with self.assertNumQueries(2):
            response = self.client.get(self.detail_url(listing))

        self.assertEqual(response.status_code, 200)
