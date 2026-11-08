"""LP-68: up to 3 branches per restaurant; each branch has its own offers,
stock and pickup address."""

from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from .branch_service import HAS_DRAFTS, HAS_LIVE_OFFERS, LAST_BRANCH, NAME_TAKEN, TOO_MANY, active_branches
from .models import Branch, Listing, Shop
from .test_browse import OPERATOR_ID, make_listing, make_shop
from .test_lp37_restaurant_apply import ApplicationTestCase, application
from .test_shops import ShopAPITestCase, make_token

LIST_URL = reverse("my-branches")
OTHER_OWNER = OPERATOR_ID + 100


def branch_url(branch):
    return reverse("my-branch", args=[getattr(branch, "pk", branch)])


def draft_data(**changes):
    start = timezone.now() + timedelta(hours=3)
    return {
        "title": "Croissant Box", "description": "Fresh", "category": "Bakery",
        "original_price": "10.00", "discounted_price": "4.00", "total_quantity": 5,
        "pickup_start": start.isoformat(), "pickup_end": (start + timedelta(hours=2)).isoformat(),
        **changes,
    }


class BranchTestCase(ShopAPITestCase):
    def setUp(self):
        self.shop = make_shop()
        self.main = self.shop.branches.get()
        self.as_owner()

    def as_owner(self, user_id=OPERATOR_ID):
        self.as_user(make_token(user_id=user_id))

    def add(self, name="Achrafieh", address="5 Sassine Square, Beirut", area="Achrafieh"):
        return self.client.post(LIST_URL, {"name": name, "address": address, "area": area}, format="json")


class EveryRestaurantHasABranchTests(BranchTestCase):
    def test_a_new_restaurant_gets_its_main_branch_from_its_address(self):
        self.assertEqual(
            (self.main.name, self.main.address, self.main.area, self.main.position),
            ("Hamra", "12 Bliss Street, Beirut", "Hamra", 1),
        )

    def test_the_owner_sees_their_branches(self):
        self.add()
        response = self.client.get(LIST_URL)

        self.assertEqual(response.status_code, 200)
        self.assertEqual([b["name"] for b in response.json()], ["Hamra", "Achrafieh"])
        self.assertEqual(response.json()[0]["draft_offers"], 0)

    def test_my_shop_lists_the_branches_too(self):
        self.add()
        names = [branch["name"] for branch in self.client.get(reverse("shop-me")).json()["branches"]]
        self.assertEqual(names, ["Hamra", "Achrafieh"])


class AddBranchTests(BranchTestCase):
    def test_up_to_three_branches(self):
        self.assertEqual(self.add().status_code, 201)
        self.assertEqual(self.add("Verdun", "1 Verdun Street, Beirut", "Verdun").status_code, 201)

        response = self.add("Jounieh", "1 Main Road, Jounieh", "Jounieh")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"detail": TOO_MANY})
        self.assertEqual(active_branches(self.shop).count(), 3)

    def test_two_branches_cannot_share_a_name(self):
        response = self.add(name="hamra")
        self.assertEqual(response.json(), {"name": NAME_TAKEN})

    def test_branch_details_are_checked(self):
        response = self.client.post(LIST_URL, {"name": "A", "address": "x", "area": ""}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(set(response.json()), {"name", "address", "area"})

        unknown = self.client.post(LIST_URL, {**dict(name="Verdun", address="1 Verdun Street", area="Verdun"),
                                              "position": 1}, format="json")
        self.assertEqual(unknown.json(), {"position": ["This field is not allowed."]})

    def test_a_removed_branch_frees_its_place(self):
        second = Branch.objects.get(pk=self.add().json()["id"])
        self.add("Verdun", "1 Verdun Street, Beirut", "Verdun")
        self.client.delete(branch_url(second))

        self.assertEqual(self.add("Jounieh", "1 Main Road, Jounieh", "Jounieh").status_code, 201)


class OwnBranchesOnlyTests(BranchTestCase):
    def test_another_restaurants_branch_is_not_found(self):
        other = make_shop(operator_user_id=OTHER_OWNER, name="Other")
        theirs = other.branches.get()

        for method in ("patch", "delete"):
            with self.subTest(method=method):
                response = getattr(self.client, method)(branch_url(theirs), {"name": "Mine now"}, format="json")
                self.assertEqual(response.status_code, 404)
        theirs.refresh_from_db()
        self.assertEqual((theirs.name, theirs.removed_at), ("Hamra", None))

    def test_customers_and_signed_out_visitors_are_refused(self):
        self.as_user(make_token(role="CUSTOMER"))
        self.assertEqual(self.client.get(LIST_URL).status_code, 403)
        self.client.credentials()
        self.assertEqual(self.client.get(LIST_URL).status_code, 401)


class EditBranchTests(BranchTestCase):
    def test_editing_the_address_updates_drafts_but_not_live_offers(self):
        draft = make_listing(self.shop, status=Listing.Status.DRAFT, branch=self.main)
        live = make_listing(self.shop, branch=self.main)

        response = self.client.patch(branch_url(self.main), {"address": "99 New Street, Beirut"}, format="json")

        self.assertEqual(response.status_code, 200)
        draft.refresh_from_db()
        live.refresh_from_db()
        self.assertEqual(draft.pickup_address, "99 New Street, Beirut")
        self.assertEqual(live.pickup_address, "12 Bliss Street, Beirut")

    def test_the_main_branch_and_the_restaurant_address_stay_the_same(self):
        self.client.patch(branch_url(self.main), {"address": "99 New Street, Beirut", "area": "Verdun"}, format="json")
        self.shop.refresh_from_db()
        self.assertEqual((self.shop.address, self.shop.area), ("99 New Street, Beirut", "Verdun"))

        self.client.patch(reverse("shop-me"), {"address": "1 Profile Street, Beirut", "area": "Hamra"}, format="json")
        self.main.refresh_from_db()
        self.assertEqual((self.main.address, self.main.area), ("1 Profile Street, Beirut", "Hamra"))

    def test_another_branch_never_changes_the_restaurant_address(self):
        second = Branch.objects.get(pk=self.add().json()["id"])
        self.client.patch(branch_url(second), {"address": "7 Other Street, Beirut"}, format="json")
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.address, "12 Bliss Street, Beirut")

    def test_renaming_to_another_branchs_name_is_refused(self):
        second = Branch.objects.get(pk=self.add().json()["id"])
        response = self.client.patch(branch_url(second), {"name": "HAMRA"}, format="json")
        self.assertEqual(response.json(), {"name": NAME_TAKEN})


class RemoveBranchTests(BranchTestCase):
    def setUp(self):
        super().setUp()
        self.second = Branch.objects.get(pk=self.add().json()["id"])

    def test_the_last_branch_cannot_be_removed(self):
        self.client.delete(branch_url(self.second))
        response = self.client.delete(branch_url(self.main))
        self.assertEqual(response.json(), {"detail": LAST_BRANCH})

    def test_a_branch_with_drafts_or_live_offers_cannot_be_removed(self):
        draft = make_listing(self.shop, status=Listing.Status.DRAFT, branch=self.second)
        self.assertEqual(self.client.delete(branch_url(self.second)).json(), {"detail": HAS_DRAFTS})

        draft.status = Listing.Status.PUBLISHED
        draft.save()
        self.assertEqual(self.client.delete(branch_url(self.second)).json(), {"detail": HAS_LIVE_OFFERS})

    def test_a_branch_whose_offers_ended_is_hidden_and_keeps_its_history(self):
        ended = timezone.now() - timedelta(hours=1)
        old = make_listing(self.shop, branch=self.second, pickup_start=ended - timedelta(hours=2), pickup_end=ended)

        self.assertEqual(self.client.delete(branch_url(self.second)).status_code, 204)

        self.second.refresh_from_db()
        old.refresh_from_db()
        self.assertIsNotNone(self.second.removed_at)
        self.assertEqual(old.branch, self.second)
        self.assertEqual([b["name"] for b in self.client.get(LIST_URL).json()], ["Hamra"])

    def test_removing_the_main_branch_makes_the_next_one_the_address(self):
        self.assertEqual(self.client.delete(branch_url(self.main)).status_code, 204)
        self.shop.refresh_from_db()
        self.assertEqual((self.shop.address, self.shop.area), ("5 Sassine Square, Beirut", "Achrafieh"))


class OffersAtBranchesTests(BranchTestCase):
    url = reverse("listings")

    def setUp(self):
        super().setUp()
        self.second = Branch.objects.get(pk=self.add().json()["id"])

    def test_an_offer_without_a_branch_goes_to_the_main_branch(self):
        response = self.client.post(self.url, draft_data(), format="json")

        self.assertEqual(response.status_code, 201, response.json())
        listing = Listing.objects.get(pk=response.json()["id"])
        self.assertEqual((listing.branch, listing.pickup_address), (self.main, "12 Bliss Street, Beirut"))

    def test_the_pickup_address_always_comes_from_the_branch(self):
        response = self.client.post(
            self.url, draft_data(branch=self.second.id, pickup_address="Somewhere else"), format="json")

        self.assertEqual(response.json()["pickup_address"], "5 Sassine Square, Beirut")
        self.assertEqual(response.json()["branch_details"]["name"], "Achrafieh")

    def test_another_restaurants_or_a_removed_branch_is_refused(self):
        other = make_shop(operator_user_id=OTHER_OWNER, name="Other").branches.get()
        third = Branch.objects.get(pk=self.add("Verdun", "1 Verdun Street, Beirut", "Verdun").json()["id"])
        self.client.delete(branch_url(third))

        for branch in (other, third, 999999):
            with self.subTest(branch=branch):
                response = self.client.post(self.url, draft_data(branch=getattr(branch, "pk", branch)), format="json")
                self.assertEqual(response.status_code, 400)
                self.assertIn("branch", response.json())
        self.assertFalse(Listing.objects.exists())

    def test_moving_a_draft_to_another_branch_moves_its_pickup_address(self):
        listing_id = self.client.post(self.url, draft_data(), format="json").json()["id"]

        response = self.client.patch(reverse("listing_detail", args=[listing_id]), {"branch": self.second.id}, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["pickup_address"], "5 Sassine Square, Beirut")


class OneOfferForSeveralBranchesTests(BranchTestCase):
    url = reverse("listings")

    def setUp(self):
        super().setUp()
        self.second = Branch.objects.get(pk=self.add().json()["id"])

    def post(self, entries, **changes):
        return self.client.post(self.url, {**draft_data(**changes), "branches": entries}, format="json")

    def test_each_branch_gets_its_own_offer_and_stock(self):
        response = self.post([
            {"branch": self.main.id, "total_quantity": 5},
            {"branch": self.second.id, "total_quantity": 3},
        ])

        self.assertEqual(response.status_code, 201, response.json())
        created = {item["branch"]: item for item in response.json()["listings"]}
        self.assertEqual(created[self.main.id]["total_quantity"], 5)
        self.assertEqual(created[self.second.id]["total_quantity"], 3)
        self.assertEqual(created[self.second.id]["pickup_address"], "5 Sassine Square, Beirut")
        self.assertEqual(Listing.objects.filter(status=Listing.Status.DRAFT).count(), 2)

    def test_changing_one_branchs_stock_leaves_the_other_alone(self):
        created = {item["branch"]: item for item in self.post([
            {"branch": self.main.id, "total_quantity": 5},
            {"branch": self.second.id, "total_quantity": 4},
        ]).json()["listings"]}
        for item in created.values():
            published = self.client.post(reverse("publish_listing", args=[item["id"]]), format="json")
            self.assertEqual(published.status_code, 200, published.json())
        url = reverse("listing_stock_adjustments", args=[created[self.main.id]["id"]])

        response = self.client.post(url, {"quantity_change": -2, "reason": "Two sold in store"}, format="json")

        self.assertEqual(response.status_code, 201, response.json())
        self.assertEqual(response.json()["listing"]["total_quantity"], 3)
        self.assertEqual(Listing.objects.get(pk=created[self.second.id]["id"]).total_quantity, 4)
        self.assertEqual(Listing.objects.get(pk=created[self.main.id]["id"]).total_quantity, 3)

    def test_one_bad_quantity_creates_nothing(self):
        response = self.post([
            {"branch": self.main.id, "total_quantity": 5},
            {"branch": self.second.id, "total_quantity": -1},
        ])

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["branches"][0], {})
        self.assertIn("total_quantity", response.json()["branches"][1])
        self.assertFalse(Listing.objects.exists())

    def test_a_shared_problem_is_shown_once(self):
        response = self.post(
            [{"branch": self.main.id, "total_quantity": 5}, {"branch": self.second.id, "total_quantity": 3}],
            discounted_price="20.00",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("discounted_price", response.json())
        self.assertNotIn("branches", response.json())

    def test_each_branch_once_and_only_your_branches(self):
        other = make_shop(operator_user_id=OTHER_OWNER, name="Other").branches.get()
        for entries in (
            [{"branch": self.main.id, "total_quantity": 1}, {"branch": self.main.id, "total_quantity": 2}],
            [],
            [{"branch": self.main.id}],
            "not a list",
        ):
            with self.subTest(entries=entries):
                self.assertEqual(self.post(entries).status_code, 400)
        response = self.post([{"branch": other.id, "total_quantity": 1}])
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Listing.objects.exists())


class CustomersSeeBranchesTests(BranchTestCase):
    def setUp(self):
        super().setUp()
        self.second = Branch.objects.get(pk=self.add().json()["id"])
        self.client.credentials()

    def test_offers_say_which_branch_and_shops_how_many(self):
        make_listing(self.shop, branch=self.second, pickup_address=self.second.address)

        item = self.client.get(reverse("listings")).json()[0]

        self.assertEqual(item["branch"], {"id": self.second.id, "name": "Achrafieh",
                                          "address": "5 Sassine Square, Beirut", "area": "Achrafieh"})
        self.assertEqual(item["shop"]["branch_count"], 2)

    def test_the_restaurant_page_lists_only_active_branches(self):
        third = Branch.objects.create(shop=self.shop, name="Old", address="Gone", area="Gone", position=3,
                                      removed_at=timezone.now())
        page = self.client.get(reverse("restaurant-page", args=[self.shop.id])).json()

        self.assertEqual([b["name"] for b in page["shop"]["branches"]], ["Hamra", "Achrafieh"])
        self.assertNotIn(third.id, [b["id"] for b in page["shop"]["branches"]])

    def test_browsing_still_takes_two_queries_with_branches(self):
        for branch in (self.main, self.second):
            make_listing(self.shop, branch=branch)
        make_listing(make_shop(operator_user_id=OTHER_OWNER, name="Other"))
        self.client.get(reverse("listings"))  # warm up

        with self.assertNumQueries(2):
            self.client.get(reverse("listings"))


class ApplicationBranchesTests(ApplicationTestCase):
    def branches(self):
        return [(b.name, b.address, b.area) for b in active_branches(Shop.objects.get())]

    def test_the_request_can_name_branch_1_and_add_two_more(self):
        others = [{"name": "Achrafieh", "address": "5 Sassine Square, Beirut", "area": "Achrafieh"},
                  {"name": "Verdun", "address": "1 Verdun Street, Beirut", "area": "Verdun"}]
        import json
        response = self.send(application(branch_name="Bliss", other_branches=json.dumps(others)))

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(self.branches(), [
            ("Bliss", "12 Bliss Street, Beirut", "Hamra"),
            ("Achrafieh", "5 Sassine Square, Beirut", "Achrafieh"),
            ("Verdun", "1 Verdun Street, Beirut", "Verdun"),
        ])

    def test_more_than_three_or_repeated_names_are_refused(self):
        import json
        four = [{"name": f"B{i}", "address": f"{i} Long Street, Beirut", "area": "Hamra"} for i in range(3)]
        self.assertEqual(self.send(application(other_branches=json.dumps(four))).status_code, 400)
        same = [{"name": "Hamra", "address": "5 Other Street, Beirut", "area": "Hamra"}]
        self.assertEqual(self.send(application(other_branches=json.dumps(same))).status_code, 400)
        self.assertFalse(Shop.objects.exists())

    def test_sending_again_updates_the_branches_in_place(self):
        import json
        two = [{"name": "Achrafieh", "address": "5 Sassine Square, Beirut", "area": "Achrafieh"}]
        self.send(application(other_branches=json.dumps(two)))
        first_ids = [b.id for b in active_branches(Shop.objects.get())]

        self.send(application(address="20 New Street, Beirut"))

        self.assertEqual(self.branches(), [("Hamra", "20 New Street, Beirut", "Hamra")])
        self.assertEqual([b.id for b in active_branches(Shop.objects.get())], first_ids[:1])

    def test_a_request_without_branches_still_gives_one(self):
        self.send(application())
        self.assertEqual(self.branches(), [("Hamra", "12 Bliss Street, Beirut", "Hamra")])
