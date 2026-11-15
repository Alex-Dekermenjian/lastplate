"""LP-29: offer counts, the stock floor, safe unpublishing and the branch's
closing time.

- The owner sees "posted · reserved · left · sold" for each offer.
- Stock can't go below what customers already reserved (HELD + CONSUMED).
- Taking an offer down hides it and stops new reservations, but orders
  already made stay valid and can still be picked up.
- Only drafts can be edited; a published offer can't.
- A branch has a closing time; a pickup end after it needs a confirm.
"""

import uuid
from datetime import datetime, time, timedelta

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from .authentication import TokenUser
from .browse import visible_listings
from .models import Allocation, Branch, Listing
from .test_browse import OPERATOR_ID, make_listing, make_shop
from .test_lp20_stock_api import TEST_KEYS, StockApiTestCase


def owner_client(user_id=OPERATOR_ID):
    client = APIClient()
    client.force_authenticate(user=TokenUser(id=user_id, role="SHOP_OPERATOR", is_staff=False))
    return client


def local(day, hour, minute=0):
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


@override_settings(INTERNAL_SERVICE_KEYS=TEST_KEYS)
class OwnerTestCase(StockApiTestCase):
    """The LP-20 set-up (3 donuts, the Reservations client) plus the owner."""

    def setUp(self):
        super().setUp()
        self.owner = owner_client()

    def reserve(self, quantity=1):
        ref = uuid.uuid4()
        response = self.post_reserve(quantity, ref)
        self.assertEqual(response.status_code, 201, response.json())
        return ref

    def mine(self):
        response = self.owner.get(reverse("my_listings"))
        self.assertEqual(response.status_code, 200)
        return next(item for item in response.json() if item["id"] == self.listing.pk)


class OfferCountsTests(OwnerTestCase):
    """Done when: each offer shows posted · reserved · left · sold."""

    def test_reserved_is_held_sold_is_consumed_and_left_is_free(self):
        self.reserve(1)                                  # reserved, not picked up yet
        sold = self.reserve(1)
        self.post_action("consume", sold)                # picked up and paid
        gone = self.reserve(1)
        self.post_action("release", gone)                # cancelled: back on sale

        offer = self.mine()

        self.assertEqual(
            (offer["total_quantity"], offer["reserved_quantity"], offer["free_quantity"], offer["sold_quantity"]),
            (3, 1, 1, 1),
        )
        self.assertEqual(offer["committed_quantity"], 2)

    def test_a_new_offer_counts_zero(self):
        offer = self.mine()

        self.assertEqual((offer["reserved_quantity"], offer["sold_quantity"], offer["free_quantity"]), (0, 0, 3))

    def test_counts_are_right_for_several_offers_in_one_query(self):
        other = make_listing(self.shop, title="Bread", total_quantity=4)
        self.reserve(2)
        self.post_reserve(listing=other)

        response = self.owner.get(reverse("my_listings"))
        by_id = {item["id"]: item for item in response.json()}

        self.assertEqual((by_id[self.listing.pk]["reserved_quantity"], by_id[self.listing.pk]["free_quantity"]), (2, 1))
        self.assertEqual((by_id[other.pk]["reserved_quantity"], by_id[other.pk]["free_quantity"]), (1, 3))


class StockFloorTests(OwnerTestCase):
    """Done when: stock can't go below what customers already reserved."""

    def adjust(self, change):
        url = reverse("listing_stock_adjustments", kwargs={"pk": self.listing.pk})
        return self.owner.post(url, {"quantity_change": change, "reason": "Recount"}, format="json")

    def test_cannot_lower_stock_below_reserved(self):
        self.reserve(2)

        response = self.adjust(-2)                       # 3 - 2 = 1 < 2 reserved

        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["code"], "below_committed")
        self.assertEqual(body["already_reserved"], 2)
        self.assertIn("customers already reserved 2 units", body["detail"])
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.total_quantity, 3)
        self.assertFalse(self.listing.stock_adjustments.exists())

    def test_picked_up_units_count_too(self):
        sold = self.reserve(2)
        self.post_action("consume", sold)

        self.assertEqual(self.adjust(-2).json()["already_reserved"], 2)

    def test_down_to_exactly_reserved_is_allowed(self):
        self.reserve(2)

        response = self.adjust(-1)

        self.assertEqual(response.status_code, 201, response.json())
        self.assertEqual(response.json()["listing"]["free_quantity"], 0)


class PublishedOffersAreNotEditableTests(OwnerTestCase):
    """Already true before LP-29; kept, and tested here."""

    def test_published_offer_cannot_be_edited(self):
        url = reverse("listing_detail", kwargs={"pk": self.listing.pk})

        response = self.owner.patch(url, {"title": "Changed", "discounted_price": "0.50"}, format="json")

        self.assertEqual(response.status_code, 404)
        self.listing.refresh_from_db()
        self.assertEqual((self.listing.title, str(self.listing.discounted_price)), ("Glazed donuts", "1.50"))

    def test_nor_its_photo(self):
        url = reverse("upload_listing_photo", kwargs={"pk": self.listing.pk})

        self.assertEqual(self.owner.post(url, {}, format="multipart").status_code, 404)

    def test_a_taken_down_offer_cannot_be_edited_either(self):
        Listing.objects.filter(pk=self.listing.pk).update(status=Listing.Status.WITHDRAWN)
        url = reverse("listing_detail", kwargs={"pk": self.listing.pk})

        self.assertEqual(self.owner.patch(url, {"title": "Changed"}, format="json").status_code, 404)

    def test_a_draft_still_can(self):
        draft = make_listing(self.shop, status=Listing.Status.DRAFT)
        url = reverse("listing_detail", kwargs={"pk": draft.pk})

        response = self.owner.patch(url, {"title": "Better title"}, format="json")

        self.assertEqual(response.status_code, 200, response.json())


class UnpublishTests(OwnerTestCase):
    """Done when: taking an offer down hides it, but existing reservations stay
    valid and can still be picked up."""

    def unpublish(self, listing=None, client=None, data=None):
        url = reverse("listing_unpublish", kwargs={"pk": (listing or self.listing).pk})
        return (client or self.owner).post(url, data or {}, format="json")

    def test_unpublish_keeps_existing_orders_and_pickup_still_works(self):
        kept = self.reserve(2)

        response = self.unpublish()

        self.assertEqual(response.status_code, 200, response.json())
        self.assertEqual(response.json()["status"], "WITHDRAWN")
        self.assertEqual(response.json()["reserved_quantity"], 2)
        allocation = Allocation.objects.get(reservation_ref=kept)
        self.assertEqual(allocation.status, Allocation.Status.HELD)

        # The order is picked up and paid after the offer was taken down.
        consumed = self.post_action("consume", kept)
        self.assertEqual(consumed.status_code, 200, consumed.json())
        allocation.refresh_from_db()
        self.assertEqual(allocation.status, Allocation.Status.CONSUMED)
        self.assertEqual(self.mine()["sold_quantity"], 2)

    def test_a_reservation_can_still_be_cancelled_after_unpublish(self):
        ref = self.reserve(1)
        self.unpublish()

        self.assertEqual(self.post_action("release", ref).status_code, 200)

    def test_customers_no_longer_see_it_and_nobody_new_can_reserve(self):
        self.unpublish()

        self.assertIsNone(self.customers_see())
        self.assertNotIn(self.listing.pk, [listing.pk for listing, _ in visible_listings()])
        refused = self.post_reserve(1)
        self.assertEqual(refused.status_code, 409)
        self.assertEqual(refused.json()["allocation"]["reject_reason"], "not_published")

    def test_stock_of_a_taken_down_offer_no_longer_changes(self):
        self.unpublish()
        url = reverse("listing_stock_adjustments", kwargs={"pk": self.listing.pk})

        response = self.owner.post(url, {"quantity_change": 2, "reason": "More"}, format="json")

        self.assertEqual(response.json()["code"], "not_published")

    def test_twice_is_fine(self):
        self.unpublish()

        self.assertEqual(self.unpublish().status_code, 200)

    def test_only_published_offers_and_only_the_owner(self):
        draft = make_listing(self.shop, status=Listing.Status.DRAFT)

        self.assertEqual(self.unpublish(draft).json()["code"], "not_published")
        self.assertEqual(self.unpublish(client=owner_client(user_id=1)).status_code, 403)
        self.assertEqual(self.unpublish(data={"status": "PUBLISHED"}).status_code, 400)
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.status, Listing.Status.PUBLISHED)


class BranchClosingTimeTests(OwnerTestCase):
    """Done when: the owner sets each branch's closing time; a pickup end after
    it needs a confirm."""

    def setUp(self):
        super().setUp()
        self.branch = Branch.objects.get(shop=self.shop)
        self.tomorrow = timezone.localdate() + timedelta(days=1)

    def set_closing(self, value, branch=None):
        url = reverse("my-branch", kwargs={"pk": (branch or self.branch).pk})
        return self.owner.patch(url, {"closing_time": value}, format="json")

    def create(self, end, start=None, **extra):
        data = {
            "title": "Bread",
            "total_quantity": 2,
            "pickup_start": (start or local(self.tomorrow, 18)).isoformat(),
            "pickup_end": end.isoformat(),
            **extra,
        }
        return self.owner.post(reverse("listings"), data, format="json")

    def test_owner_sets_and_clears_a_branch_closing_time(self):
        response = self.set_closing("22:00")

        self.assertEqual(response.status_code, 200, response.json())
        self.assertEqual(response.json()["closing_time"], "22:00:00")
        self.branch.refresh_from_db()
        self.assertEqual(self.branch.closing_time, time(22, 0))

        self.assertIsNone(self.set_closing(None).json()["closing_time"])

    def test_a_new_branch_can_have_one(self):
        response = self.owner.post(reverse("my-branches"), {
            "name": "Verdun", "address": "1 Verdun Street", "area": "Verdun", "closing_time": "21:30",
        }, format="json")

        self.assertEqual(response.status_code, 201, response.json())
        self.assertEqual(response.json()["closing_time"], "21:30:00")

    def test_the_branch_list_and_the_offer_show_it(self):
        self.set_closing("22:00")

        branches = self.owner.get(reverse("my-branches")).json()
        self.assertEqual(branches[0]["closing_time"], "22:00:00")
        self.assertEqual(self.owner.get(reverse("shop-me")).json()["branches"][0]["closing_time"], "22:00:00")
        Listing.objects.filter(pk=self.listing.pk).update(branch=self.branch)
        self.assertEqual(self.mine()["branch_details"]["closing_time"], "22:00")

    def test_a_pickup_end_after_closing_needs_a_confirm(self):
        self.set_closing("22:00")

        refused = self.create(local(self.tomorrow, 23))

        self.assertEqual(refused.status_code, 400)
        self.assertIn("after", refused.json()["confirm_after_closing"][0])
        self.assertIn("22:00", refused.json()["confirm_after_closing"][0])
        self.assertFalse(Listing.objects.filter(title="Bread").exists())

        confirmed = self.create(local(self.tomorrow, 23), confirm_after_closing=True)
        self.assertEqual(confirmed.status_code, 201, confirmed.json())

    def test_ending_by_closing_needs_no_confirm(self):
        self.set_closing("22:00")

        self.assertEqual(self.create(local(self.tomorrow, 22)).status_code, 201)
        self.assertEqual(self.create(local(self.tomorrow, 21, 30)).status_code, 201)

    def test_no_closing_time_means_no_check(self):
        self.assertEqual(self.create(local(self.tomorrow, 23, 30)).status_code, 201)

    def test_a_branch_open_past_midnight(self):
        self.set_closing("01:00")
        day_after = self.tomorrow + timedelta(days=1)

        self.assertEqual(self.create(local(day_after, 0, 30), start=local(self.tomorrow, 20)).status_code, 201)
        self.assertEqual(self.create(local(day_after, 2), start=local(self.tomorrow, 20)).status_code, 400)

    def test_each_branch_is_checked_with_its_own_closing_time(self):
        self.set_closing("23:30")
        early = Branch.objects.create(shop=self.shop, name="Verdun", address="1 Verdun Street", area="Verdun",
                                      position=2, closing_time=time(20, 0))

        response = self.owner.post(reverse("listings"), {
            "title": "Bread",
            "pickup_start": local(self.tomorrow, 18).isoformat(),
            "pickup_end": local(self.tomorrow, 23).isoformat(),
            "branches": [{"branch": self.branch.pk, "total_quantity": 2}, {"branch": early.pk, "total_quantity": 1}],
        }, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertIn("Verdun closes at 20:00", response.json()["confirm_after_closing"][0])

    def test_editing_something_else_does_not_ask_again(self):
        self.set_closing("22:00")
        created = self.create(local(self.tomorrow, 23), confirm_after_closing=True).json()
        url = reverse("listing_detail", kwargs={"pk": created["id"]})

        self.assertEqual(self.owner.patch(url, {"title": "Brown bread"}, format="json").status_code, 200)
        moved = self.owner.patch(url, {"pickup_end": local(self.tomorrow, 23, 30).isoformat()}, format="json")
        self.assertEqual(moved.status_code, 400)
        self.assertIn("confirm_after_closing", moved.json())


class NewShopBranchTests(OwnerTestCase):
    def test_branches_start_without_a_closing_time(self):
        shop = make_shop(operator_user_id=555, name="Olive Tree")

        self.assertIsNone(Branch.objects.get(shop=shop).closing_time)
