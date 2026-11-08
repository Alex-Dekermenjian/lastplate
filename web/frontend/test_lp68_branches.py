"""LP-68 (website): up to 3 branches, each like its own store: the owner's
Branches page, posting food to several branches, the application, and what
customers see ("ZWZ · Hamra", branch tabs, one branch per cart)."""

import json

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from .test_accounts import APPROVED_SHOP, WebAuthTestCase, patch_service
from .test_browse_offers import offer
from .test_lp39_restaurant_page import SHOP as PUBLIC_SHOP
from .test_lp56_login_signup import CUSTOMER
from .test_lp57_console import ADMIN, SHOP as CONSOLE_SHOP, ConsoleTestCase
from .test_lp62_cart import CartTestCase

HAMRA = {"id": 11, "name": "Hamra", "address": "12 Bliss Street, Beirut", "area": "Hamra",
         "position": 1, "draft_offers": 0, "live_offers": 2}
ACHRAFIEH = {"id": 12, "name": "Achrafieh", "address": "5 Sassine Square, Beirut", "area": "Achrafieh",
             "position": 2, "draft_offers": 0, "live_offers": 0}
VERDUN = {"id": 13, "name": "Verdun", "address": "1 Verdun Street, Beirut", "area": "Verdun",
          "position": 3, "draft_offers": 1, "live_offers": 0}
TWO_BRANCH_SHOP = {**APPROVED_SHOP, "branches": [HAMRA, ACHRAFIEH]}
ONE_BRANCH_SHOP = {**APPROVED_SHOP, "branches": [HAMRA]}
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


class OwnerTestCase(WebAuthTestCase):
    shop = TWO_BRANCH_SHOP

    def setUp(self):
        self.sign_in_session()
        patcher = patch_service("my_shop", return_value=(200, self.shop))
        patcher.start()
        self.addCleanup(patcher.stop)


# ---------- The Branches page ----------

class BranchesPageTests(OwnerTestCase):
    url = reverse("branches")

    def get(self, branches):
        with patch_service("my_branches", return_value=(200, branches)):
            return self.client.get(self.url)

    def test_lists_branches_with_their_offers_and_what_blocks_removing(self):
        page = self.get([HAMRA, ACHRAFIEH, VERDUN])

        for text in ("Hamra", "Main", "12 Bliss Street, Beirut", "2 live offers", "Achrafieh", "1 draft"):
            self.assertContains(page, text)
        self.assertContains(page, "It has offers customers can still order")
        self.assertContains(page, "It has draft offers")
        self.assertContains(page, reverse("branch-remove", args=[12]))
        self.assertNotContains(page, reverse("branch-remove", args=[11]))
        self.assertNotContains(page, "+ Add a branch")
        self.assertContains(page, "the most a restaurant can have")

    def test_the_only_branch_can_never_be_removed(self):
        page = self.get([{**HAMRA, "live_offers": 0}])
        self.assertContains(page, "Your restaurant needs at least one branch.")
        self.assertNotContains(page, reverse("branch-remove", args=[11]))
        self.assertContains(page, "+ Add a branch")

    def test_adding_a_branch_sends_a_place_from_the_list(self):
        data = {"new-name": "Achrafieh", "new-address": "5 Sassine Square, Beirut", "new-area": "Achrafieh"}
        with patch_service("my_branches", return_value=(200, [HAMRA])), \
                patch_service("add_branch", return_value=(201, ACHRAFIEH)) as add:
            response = self.client.post(self.url, data)

        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        add.assert_called_once_with("access-token-1", {
            "name": "Achrafieh", "address": "5 Sassine Square, Beirut", "area": "Achrafieh"})

    def test_a_typed_area_or_marketplace_refusal_is_shown(self):
        with patch_service("my_branches", return_value=(200, [HAMRA])), patch_service("add_branch") as add:
            response = self.client.post(self.url, {"new-name": "X1", "new-address": "5 Sassine Square", "new-area": "sassine"})
        add.assert_not_called()
        self.assertContains(response, "Choose your area from the list.", status_code=400)

        refusal = (400, {"name": ["You already have a branch with this name."]})
        with patch_service("my_branches", return_value=(200, [HAMRA])), patch_service("add_branch", return_value=refusal):
            response = self.client.post(self.url, {"new-name": "Hamra", "new-address": "5 Sassine Square", "new-area": "Hamra"})
        self.assertContains(response, "You already have a branch with this name.", status_code=400)

    def test_editing_and_removing(self):
        data = {"branch-12-name": "Sassine", "branch-12-address": "5 Sassine Square, Beirut", "branch-12-area": "Achrafieh"}
        with patch_service("my_branches", return_value=(200, [HAMRA, ACHRAFIEH])), \
                patch_service("update_branch", return_value=(200, ACHRAFIEH)) as update:
            response = self.client.post(reverse("branch-edit", args=[12]), data)
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        update.assert_called_once_with("access-token-1", 12, {
            "name": "Sassine", "address": "5 Sassine Square, Beirut", "area": "Achrafieh"})

        with patch_service("remove_branch", return_value=(204, {})) as remove:
            self.client.post(reverse("branch-remove", args=[12]))
        remove.assert_called_once_with("access-token-1", 12)

        blocked = (400, {"detail": "This branch has draft offers. Move them to another branch or delete them first."})
        with patch_service("remove_branch", return_value=blocked), patch_service("my_branches", return_value=(200, [HAMRA])):
            response = self.client.post(reverse("branch-remove", args=[13]), follow=True)
        self.assertContains(response, "This branch has draft offers.")

    def test_the_workspace_links_to_branches(self):
        with patch_service("my_listings", return_value=(200, [])):
            page = self.client.get(reverse("workspace"))
        self.assertContains(page, reverse("branches"))
        self.assertContains(page, "Branches (2 of 3)")


# ---------- Posting food to branches ----------

ITEM_POST = {
    "title": "Croissants", "description": "Fresh", "category": "Bakery",
    "original_price": "10", "discounted_price": "4",
    "pickup_start": "2026-11-09T17:00", "pickup_end": "2026-11-09T19:00",
}


class SeveralBranchesOfferTests(OwnerTestCase):
    url = reverse("create-listing")

    def test_the_form_asks_which_branches_and_a_quantity_each(self):
        page = self.client.get(self.url, {"kind": "ITEM"})

        self.assertContains(page, "Which branches have this offer?")
        for name in ('name="branch_11"', 'name="quantity_11"', 'name="branch_12"', 'name="quantity_12"'):
            self.assertContains(page, name)
        self.assertNotContains(page, 'name="total_quantity"')
        self.assertNotContains(page, 'name="pickup_address"')

    def test_each_ticked_branch_is_sent_with_its_quantity(self):
        created = (201, {"listings": [{"id": 1}, {"id": 2}]})
        with patch_service("create_listing", return_value=created) as create:
            response = self.client.post(self.url, {**ITEM_POST, "branch_11": "1", "quantity_11": "5",
                                                   "branch_12": "1", "quantity_12": "3", "quantity_13": "9"})

        self.assertRedirects(response, reverse("workspace"), fetch_redirect_response=False)
        sent = create.call_args.args[1]
        self.assertEqual(sent["branches"], [{"branch": 11, "total_quantity": "5"}, {"branch": 12, "total_quantity": "3"}])
        self.assertNotIn("total_quantity", sent)
        self.assertNotIn("pickup_address", sent)

    def test_an_unticked_branch_is_left_out_and_none_ticked_is_refused(self):
        with patch_service("create_listing", return_value=(201, {"listings": [{"id": 1}]})) as create:
            self.client.post(self.url, {**ITEM_POST, "branch_12": "1", "quantity_11": "5", "quantity_12": "2"})
        self.assertEqual(create.call_args.args[1]["branches"], [{"branch": 12, "total_quantity": "2"}])

        with patch_service("create_listing") as create:
            response = self.client.post(self.url, ITEM_POST)
        create.assert_not_called()
        self.assertContains(response, "Choose at least one branch for this offer.", status_code=400)

    def test_the_photo_goes_on_every_branchs_offer(self):
        photo = SimpleUploadedFile("food.png", PNG, content_type="image/png")
        with patch_service("create_listing", return_value=(201, {"listings": [{"id": 1}, {"id": 2}]})), \
                patch_service("upload_listing_photo", return_value=(200, {})) as upload:
            self.client.post(self.url, {**ITEM_POST, "branch_11": "1", "branch_12": "1", "photo": photo})

        self.assertEqual([call.args[1] for call in upload.call_args_list], [1, 2])

    def test_a_problem_at_one_branch_is_named(self):
        refusal = (400, {"branches": [{}, {"total_quantity": ["Stock cannot be negative."]}]})
        with patch_service("create_listing", return_value=refusal):
            response = self.client.post(self.url, {**ITEM_POST, "branch_11": "1", "quantity_11": "5",
                                                   "branch_12": "1", "quantity_12": "-1"})

        self.assertContains(response, "Achrafieh (quantity)", status_code=400)
        self.assertContains(response, "Stock cannot be negative.", status_code=400)
        self.assertContains(response, 'name="branch_12" value="1" checked', status_code=400)


class OneBranchOfferTests(OwnerTestCase):
    shop = ONE_BRANCH_SHOP
    url = reverse("create-listing")

    def test_one_branch_works_as_before_and_says_where(self):
        page = self.client.get(self.url, {"kind": "ITEM"})
        self.assertContains(page, "Pickup at")
        self.assertContains(page, 'name="total_quantity"')
        self.assertNotContains(page, "Which branches have this offer?")

        with patch_service("create_listing", return_value=(201, {"id": 1})) as create:
            self.client.post(self.url, {**ITEM_POST, "total_quantity": "4"})
        sent = create.call_args.args[1]
        self.assertEqual((sent["branch"], sent["total_quantity"]), (11, "4"))


class MoveDraftTests(OwnerTestCase):
    def test_a_draft_can_move_to_another_branch_of_the_restaurant(self):
        draft = {"id": 42, "status": "DRAFT", "kind": "ITEM", "title": "Box", "branch": 11,
                 "branch_details": {"id": 11, "name": "Hamra"}}
        url = reverse("edit-listing", args=[42])
        with patch_service("my_listings", return_value=(200, [draft])):
            page = self.client.get(url)
        self.assertContains(page, '<option value="11" selected>')

        for posted, expected in (("12", 12), ("999", None)):
            with self.subTest(branch=posted), patch_service("my_listings", return_value=(200, [draft])), \
                    patch_service("edit_listing", return_value=(200, draft)) as edit:
                self.client.post(url, {**ITEM_POST, "branch": posted})
            self.assertEqual(edit.call_args.args[2].get("branch"), expected)


# ---------- The application ----------

class ApplicationBranchesTests(WebAuthTestCase):
    def setUp(self):
        self.sign_in_session()
        patcher = patch_service("my_shop", return_value=(404, {}))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client.post(reverse("restaurant-apply", args=[1]),
                         {"name": "Cedar", "category": "Bakery", "public_contact": "+961 1 234 567"})

    def step2(self, **extra):
        return self.client.post(reverse("restaurant-apply", args=[2]),
                                {"address": "12 Bliss Street, Beirut", "area": "Hamra", **extra})

    def test_step_2_offers_two_more_branches(self):
        page = self.client.get(reverse("restaurant-apply", args=[2]))
        self.assertContains(page, "+ Add another branch (optional)")
        self.assertContains(page, "+ Add a third branch (optional)")
        self.assertContains(page, 'name="branch_name"')

    def test_a_half_filled_branch_or_a_repeated_name_is_refused(self):
        response = self.step2(other_1_name="Achrafieh")
        self.assertContains(response, "Fill in the name, address and area, or leave all three empty.")

        response = self.step2(other_1_name="Hamra", other_1_address="5 Sassine Square", other_1_area="Achrafieh")
        self.assertContains(response, "Give each branch a different name.")

    def test_the_branches_are_sent_with_the_request(self):
        self.step2(branch_name="Bliss", other_1_name="Achrafieh", other_1_address="5 Sassine Square, Beirut",
                   other_1_area="Achrafieh")
        photo = SimpleUploadedFile("cover.png", PNG, content_type="image/png")
        with patch_service("submit_application", return_value=(201, APPROVED_SHOP)) as submit:
            self.client.post(reverse("restaurant-apply", args=[3]), {"photo": photo, "accept_conditions": "on"})

        fields = submit.call_args.args[1]
        self.assertEqual(fields["branch_name"], "Bliss")
        self.assertEqual(json.loads(fields["other_branches"]), [
            {"name": "Achrafieh", "address": "5 Sassine Square, Beirut", "area": "Achrafieh"}])


# ---------- What customers see ----------

def public_offer(branch, branch_count=2, **changes):
    shop = {"id": 3, "name": "ZWZ", "area": "Hamra", "public_contact": "+961 1 234 567", "branch_count": branch_count}
    return offer(shop=shop, branch=branch, **changes)


class CustomerPagesTests(WebAuthTestCase):
    def test_cards_say_which_branch_when_there_are_several(self):
        with patch_service("browse_listings", return_value=(200, [public_offer(ACHRAFIEH)])):
            page = self.client.get(reverse("browse-offers"))
        self.assertContains(page, "ZWZ · Achrafieh")
        self.assertContains(page, "· Achrafieh")

        with patch_service("browse_listings", return_value=(200, [public_offer(HAMRA, branch_count=1)])):
            page = self.client.get(reverse("browse-offers"))
        self.assertNotContains(page, "ZWZ · Hamra")

    def restaurant(self, query=None):
        shop = {**PUBLIC_SHOP, "name": "ZWZ", "branches": [HAMRA, ACHRAFIEH]}
        offers = [
            public_offer(HAMRA, id=1, title="Hamra bread"),
            public_offer(ACHRAFIEH, id=2, title="Achrafieh cake"),
            public_offer(None, id=3, title="Old offer"),  # from before branches: the main branch's
        ]
        with patch_service("restaurant_page", return_value=(200, {"shop": shop, "offers": offers})):
            return self.client.get(reverse("restaurant-detail", args=[3]), query or {})

    def test_the_restaurant_page_shows_one_branch_at_a_time(self):
        page = self.restaurant()
        self.assertContains(page, "Branches of ZWZ")
        self.assertContains(page, "Hamra bread")
        self.assertContains(page, "Old offer")
        self.assertNotContains(page, "Achrafieh cake")
        self.assertContains(page, "12 Bliss Street, Beirut")

        page = self.restaurant({"branch": "12"})
        self.assertContains(page, "Achrafieh cake")
        self.assertNotContains(page, "Hamra bread")
        self.assertContains(page, "5 Sassine Square, Beirut")
        self.assertContains(page, '?branch=12" aria-current="page"')

    def test_an_unknown_branch_shows_the_main_one(self):
        self.assertContains(self.restaurant({"branch": "999"}), "Hamra bread")

    def test_adding_to_the_cart_comes_back_to_the_same_branch(self):
        page = self.restaurant({"branch": "12"})
        self.assertContains(page, "back=/restaurants/3/%3Fbranch%3D12")


class OneBranchCartTests(CartTestCase):
    def setUp(self):
        self.sign_in_as(CUSTOMER)

    def test_another_branch_of_the_same_restaurant_asks_first(self):
        hamra = public_offer(HAMRA, id=42, title="Croissant Box", free_quantity=5)
        achrafieh = public_offer(ACHRAFIEH, id=43, title="Bread Bag", free_quantity=5)
        changes = {42: hamra, 43: achrafieh}
        self.add(42, changes)

        response = self.add(43, changes)

        self.assertContains(response, "Start a new cart?")
        self.assertContains(response, "ZWZ · Hamra")
        self.assertEqual(self.lines(), {"42": 1})

        self.add(43, changes, start_new="1")
        self.assertEqual(self.lines(), {"43": 1})
        self.assertContains(self.view(changes), "ZWZ · Achrafieh")

    def test_a_cart_from_before_branches_keeps_working(self):
        self.add(42)  # an offer without a branch
        hamra = public_offer(HAMRA, id=43, title="Bread Bag", free_quantity=5)
        hamra["shop"]["id"] = 3

        response = self.add(43, {43: hamra})

        self.assertNotContains(response, "Start a new cart?", status_code=302)
        self.assertEqual(self.lines(), {"42": 1, "43": 1})


class ConsoleBranchesTests(ConsoleTestCase):
    def test_the_admin_sees_every_branch_it_approves(self):
        self.sign_in_as(ADMIN)
        shop = {**CONSOLE_SHOP, "branches": [HAMRA, ACHRAFIEH]}
        with patch_service("admin_shop", return_value=(200, shop)):
            page = self.client.get(reverse("console-restaurant", args=[3]))
        self.assertContains(page, "Branches (2)")
        self.assertContains(page, "5 Sassine Square, Beirut")
        self.assertContains(page, "Approving the restaurant approves all its branches.")
