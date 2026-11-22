"""LP-82: customers save favourite restaurants and see their offers today."""

from datetime import timedelta

from django.db import IntegrityError, transaction
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Favourite, Listing, Shop
from .test_browse import make_listing, make_shop
from .test_shops import TEST_PUBLIC_KEY, ShopAPITestCase, make_token

CUSTOMER_ID = 501
OTHER_CUSTOMER_ID = 502


@override_settings(JWT_PUBLIC_KEY=TEST_PUBLIC_KEY)
class FavouritesTestCase(ShopAPITestCase):
    def setUp(self):
        self.shop = make_shop(operator_user_id=1, name="Cedar Bakery")
        self.other_shop = make_shop(operator_user_id=2, name="Olive Café")
        self.as_customer(CUSTOMER_ID)

    def as_customer(self, user_id):
        self.as_user(make_token(user_id=user_id, role="CUSTOMER"))

    def toggle(self, shop):
        return self.client.post(reverse("favourite-toggle", args=[getattr(shop, "pk", shop)]))

    def shop_ids(self):
        response = self.client.get(reverse("favourite-shops"))
        self.assertEqual(response.status_code, 200)
        return [shop["id"] for shop in response.json()["shops"]]

    def offers(self):
        response = self.client.get(reverse("favourite-offers"))
        self.assertEqual(response.status_code, 200)
        return response.json()


class ToggleTests(FavouritesTestCase):
    def test_pressing_once_saves_and_twice_removes(self):
        first = self.toggle(self.shop)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json(), {"shop_id": self.shop.id, "favourite": True})
        self.assertEqual(self.shop_ids(), [self.shop.id])

        second = self.toggle(self.shop)
        self.assertEqual(second.json(), {"shop_id": self.shop.id, "favourite": False})
        self.assertEqual(self.shop_ids(), [])
        self.assertFalse(Favourite.objects.exists())

        # And a third press saves it again.
        self.assertTrue(self.toggle(self.shop).json()["favourite"])

    def test_a_restaurant_is_saved_once_per_customer(self):
        Favourite.objects.create(customer_user_id=CUSTOMER_ID, shop=self.shop)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Favourite.objects.create(customer_user_id=CUSTOMER_ID, shop=self.shop)
        # Another customer may save the same restaurant.
        Favourite.objects.create(customer_user_id=OTHER_CUSTOMER_ID, shop=self.shop)

    def test_hidden_or_unknown_restaurants_cannot_be_saved(self):
        pending = make_shop(operator_user_id=3, approval_status=Shop.ApprovalStatus.PENDING)
        suspended = make_shop(operator_user_id=4, suspended_at=timezone.now())
        for shop in (pending, suspended, 999999):
            with self.subTest(shop=shop):
                response = self.toggle(shop)
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.json()["code"], "not_found")
        self.assertFalse(Favourite.objects.exists())

    def test_a_saved_restaurant_that_was_suspended_can_still_be_removed(self):
        self.toggle(self.shop)
        Shop.objects.filter(pk=self.shop.pk).update(suspended_at=timezone.now())

        self.assertEqual(self.shop_ids(), [])  # hidden from the list
        self.assertFalse(self.toggle(self.shop).json()["favourite"])
        self.assertFalse(Favourite.objects.exists())


class OwnFavouritesOnlyTests(FavouritesTestCase):
    def test_a_customer_only_sees_their_own_favourites(self):
        self.toggle(self.shop)
        make_listing(self.shop, title="Cedar Box")

        self.as_customer(OTHER_CUSTOMER_ID)
        self.toggle(self.other_shop)
        make_listing(self.other_shop, title="Olive Box")

        self.assertEqual(self.shop_ids(), [self.other_shop.id])
        self.assertEqual([o["title"] for o in self.offers()["offers"]], ["Olive Box"])

        self.as_customer(CUSTOMER_ID)
        self.assertEqual(self.shop_ids(), [self.shop.id])
        self.assertEqual([o["title"] for o in self.offers()["offers"]], ["Cedar Box"])

    def test_removing_mine_leaves_theirs(self):
        self.toggle(self.shop)
        self.as_customer(OTHER_CUSTOMER_ID)
        self.toggle(self.shop)
        self.toggle(self.shop)  # removes only the other customer's

        self.assertEqual(
            list(Favourite.objects.values_list("customer_user_id", flat=True)), [CUSTOMER_ID]
        )

    def test_the_customer_comes_from_the_token_not_the_request(self):
        self.client.post(
            reverse("favourite-toggle", args=[self.shop.id]),
            {"customer_user_id": OTHER_CUSTOMER_ID},
            format="json",
        )
        self.assertEqual(Favourite.objects.get().customer_user_id, CUSTOMER_ID)


class CustomersOnlyTests(FavouritesTestCase):
    def urls(self):
        return [
            ("post", reverse("favourite-toggle", args=[self.shop.id])),
            ("get", reverse("favourite-shops")),
            ("get", reverse("favourite-offers")),
        ]

    def test_shop_operators_and_admins_are_refused(self):
        tokens = {
            "shop operator": make_token(user_id=1, role="SHOP_OPERATOR"),
            "admin": make_token(user_id=99, role="ADMIN"),
            "staff customer": make_token(user_id=98, role="CUSTOMER", is_staff=True),
        }
        for who, token in tokens.items():
            self.as_user(token)
            for method, url in self.urls():
                with self.subTest(who=who, url=url):
                    self.assertEqual(getattr(self.client, method)(url).status_code, 403)
        self.assertFalse(Favourite.objects.exists())

    def test_signed_out_callers_are_refused(self):
        self.client.credentials()
        for method, url in self.urls():
            with self.subTest(url=url):
                self.assertEqual(getattr(self.client, method)(url).status_code, 401)

    def test_toggle_is_post_only(self):
        self.assertEqual(self.client.get(reverse("favourite-toggle", args=[self.shop.id])).status_code, 405)


class FavouriteOffersTodayTests(FavouritesTestCase):
    def test_only_offers_available_today_from_favourites_are_listed(self):
        self.toggle(self.shop)
        now = timezone.now()
        live = make_listing(self.shop, title="Live now")
        make_listing(self.other_shop, title="Not a favourite")
        make_listing(self.shop, title="Draft", status=Listing.Status.DRAFT)
        make_listing(self.shop, title="Withdrawn", status=Listing.Status.WITHDRAWN)
        make_listing(self.shop, title="Sold out", total_quantity=0)
        make_listing(self.shop, title="Ended", pickup_start=now - timedelta(hours=3),
                     pickup_end=now - timedelta(minutes=1))
        make_listing(self.shop, title="Tomorrow", pickup_start=now + timedelta(days=1),
                     pickup_end=now + timedelta(days=1, hours=2))

        body = self.offers()

        self.assertTrue(body["has_favourites"])
        self.assertEqual([o["id"] for o in body["offers"]], [live.id])
        offer = body["offers"][0]
        self.assertEqual(offer["shop"]["id"], self.shop.id)
        self.assertEqual(offer["free_quantity"], 5)

    def test_offers_of_a_suspended_or_rejected_favourite_are_hidden(self):
        self.toggle(self.shop)
        self.toggle(self.other_shop)
        make_listing(self.shop)
        make_listing(self.other_shop)
        Shop.objects.filter(pk=self.shop.pk).update(suspended_at=timezone.now())
        Shop.objects.filter(pk=self.other_shop.pk).update(approval_status=Shop.ApprovalStatus.REJECTED)

        self.assertEqual(self.offers()["offers"], [])

    def test_no_favourites_says_so(self):
        make_listing(self.shop)
        self.assertEqual(self.offers(), {"has_favourites": False, "offers": []})

    def test_favourites_with_nothing_today(self):
        self.toggle(self.shop)
        self.assertEqual(self.offers(), {"has_favourites": True, "offers": []})
