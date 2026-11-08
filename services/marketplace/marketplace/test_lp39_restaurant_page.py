"""LP-39: a customer opens a restaurant and sees everything it has left."""

from datetime import timedelta
from unittest import mock

from django.urls import reverse
from django.utils import timezone

from .models import Listing, Shop
from .serializers import LASTBAG
from .test_browse import MarketplaceTestCase, make_listing, make_shop
from .test_lp37_restaurant_apply import upload


class RestaurantPageTests(MarketplaceTestCase):
    def url(self, shop):
        return reverse("restaurant-page", kwargs={"pk": getattr(shop, "pk", shop)})

    def test_shows_the_restaurant_and_its_live_offers_without_sign_in(self):
        soon = make_listing(self.shop, title="Croissant Box")
        later = make_listing(
            self.shop, title="Bread Bag",
            pickup_start=soon.pickup_start + timedelta(hours=1), pickup_end=soon.pickup_end + timedelta(hours=1),
        )

        response = self.client.get(self.url(self.shop))

        self.assertEqual(response.status_code, 200)
        shop = response.json()["shop"]
        self.assertEqual(shop, {
            "id": self.shop.id, "name": "Cedar Bakery", "area": "Hamra", "public_contact": "+961 1 234 567",
            "address": "12 Bliss Street, Beirut", "category": "Other", "photo_display": None,
            "logo_display": None,
        })
        offers = response.json()["offers"]
        self.assertEqual([o["id"] for o in offers], [soon.id, later.id])
        self.assertEqual(offers[0]["kind"], "ITEM")
        self.assertEqual(offers[0]["free_quantity"], 5)

    def test_the_restaurant_logo_is_sent_for_its_round_badge(self):
        """LP-66: the logo from the LP-37 request reaches the restaurant page."""
        self.shop.logo.save("logo.png", upload("logo.png", "PNG", "image/png"))

        shop = self.client.get(self.url(self.shop)).json()["shop"]

        self.assertEqual(shop["logo_display"], self.shop.logo.url)
        self.assertTrue(shop["logo_display"].endswith(".png"))

    def test_a_stale_token_does_not_block_the_page(self):
        self.client.credentials(HTTP_AUTHORIZATION="Bearer expired")
        self.assertEqual(self.client.get(self.url(self.shop)).status_code, 200)

    def test_hidden_offers_are_left_out(self):
        other = make_shop(operator_user_id=2, name="Other Bakery")
        make_listing(self.shop, status=Listing.Status.DRAFT)
        make_listing(self.shop, status=Listing.Status.WITHDRAWN)
        make_listing(self.shop, total_quantity=0)
        ended = timezone.now() - timedelta(hours=1)
        make_listing(self.shop, pickup_start=ended - timedelta(hours=2), pickup_end=ended)
        make_listing(other)

        self.assertEqual(self.client.get(self.url(self.shop)).json()["offers"], [])

    def test_pending_rejected_and_unknown_restaurants_are_404(self):
        pending = make_shop(operator_user_id=3, approval_status=Shop.ApprovalStatus.PENDING)
        rejected = make_shop(operator_user_id=4, approval_status=Shop.ApprovalStatus.REJECTED)
        for shop in (pending, rejected, 999999):
            make_listing(pending)
            with self.subTest(shop=shop):
                response = self.client.get(self.url(shop))
                self.assertEqual(response.status_code, 404)
                self.assertNotIn("offers", response.json())

    def test_private_fields_are_never_shown(self):
        make_listing(self.shop)
        body = self.client.get(self.url(self.shop)).content.decode()
        for private in ("operator_user_id", "approval_status", "rejection_reason", "total_quantity", '"status"'):
            self.assertNotIn(private, body)

    def test_lastbags_come_first(self):
        item = make_listing(self.shop, title="Croissant Box")
        bag = make_listing(self.shop, title="Evening LastBag",
                           pickup_start=item.pickup_start + timedelta(hours=1),
                           pickup_end=item.pickup_end + timedelta(hours=1))

        # Listing.kind arrives with LP-38; simulate it on the bag.
        original = Listing.__init__

        def with_kind(listing, *args, **kwargs):
            original(listing, *args, **kwargs)
            if listing.pk == bag.pk:
                listing.kind = LASTBAG
                listing.typical_contents = "Bread, pastries"

        with mock.patch.object(Listing, "__init__", with_kind):
            offers = self.client.get(self.url(self.shop)).json()["offers"]

        self.assertEqual([o["id"] for o in offers], [bag.id, item.id])
        self.assertEqual(offers[0]["kind"], LASTBAG)
        self.assertEqual(offers[0]["typical_contents"], "Bread, pastries")

    def test_browse_offers_link_to_their_restaurant(self):
        make_listing(self.shop)
        self.assertEqual(self.client.get(self.list_url).json()[0]["shop"]["id"], self.shop.id)
