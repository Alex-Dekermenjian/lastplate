"""The type of food comes from one fixed list, so it is written the same way
everywhere ("Bakery", never "bakery" or "BAKERY")."""

from django.test import SimpleTestCase
from django.urls import reverse

from .food_types import FOOD_TYPES, canonical, type_choices, type_of
from .test_accounts import ApprovedShopMixin, WebAuthTestCase, patch_service
from .test_edit_listing import DRAFT_LISTING

ITEM = {
    "title": "Croissants", "description": "Fresh", "original_price": "10.00", "discounted_price": "5.00",
    "total_quantity": "3", "pickup_start": "2026-11-05T17:00", "pickup_end": "2026-11-05T19:00",
}


class FoodTypeListTests(SimpleTestCase):

    def test_one_spelling_whatever_the_capitals_and_spaces(self):
        for typed in ("Bakery", "bakery", " BAKERY ", "bakery\t"):
            self.assertEqual(canonical(typed), "Bakery")
        self.assertEqual(canonical("pastry  &  desserts"), "Pastry & desserts")
        self.assertIsNone(canonical("Bakeries"))

    def test_what_an_offer_counts_as(self):
        self.assertEqual(type_of("bakery"), "Bakery")
        self.assertEqual(type_of("croissant stuff"), "Other")
        self.assertEqual(type_of(""), "")
        self.assertEqual(type_of(None), "")

    def test_the_list_has_no_two_names_that_differ_only_by_capitals(self):
        self.assertEqual(len({name.casefold() for name in FOOD_TYPES}), len(FOOD_TYPES))
        self.assertEqual(FOOD_TYPES[-1], "Other")

    def test_an_older_typed_category_is_kept_as_a_choice(self):
        self.assertEqual(type_choices("croissant stuff")[0], ("croissant stuff", "croissant stuff (current)"))
        self.assertEqual(type_choices("bakery"), type_choices())


class NewOfferTypeTests(ApprovedShopMixin, WebAuthTestCase):

    def setUp(self):
        self.url = reverse("create-listing")
        self.sign_in_session()

    def test_the_form_has_a_type_of_food_list(self):
        response = self.client.get(self.url)

        self.assertContains(response, '<label for="category">Type of food</label>', html=True)
        self.assertContains(response, '<select id="category" name="category">')
        for name in FOOD_TYPES:
            self.assertContains(response, f'<option value="{name}">{name}</option>', html=True)
        self.assertNotContains(response, 'name="category" type="text"')

    def test_the_list_spelling_is_sent(self):
        with patch_service("create_listing", return_value=(201, {"id": 1, "status": "DRAFT"})) as api:
            self.client.post(self.url, {**ITEM, "category": "  bakery "})

        self.assertEqual(api.call_args.args[1]["category"], "Bakery")

    def test_a_type_not_in_the_list_is_refused(self):
        with patch_service("create_listing") as api:
            response = self.client.post(self.url, {**ITEM, "category": "Bakeries"})

        self.assertContains(response, "Choose a type of food from the list.", status_code=400)
        api.assert_not_called()

    def test_a_draft_can_still_be_saved_without_a_type(self):
        with patch_service("create_listing", return_value=(201, {"id": 1, "status": "DRAFT"})) as api:
            self.client.post(self.url, {**ITEM, "category": ""})

        api.assert_called_once()


class EditOfferTypeTests(ApprovedShopMixin, WebAuthTestCase):

    def setUp(self):
        self.url = reverse("edit-listing", args=[42])
        self.sign_in_session()

    def post(self, draft, category):
        with patch_service("my_listings", return_value=(200, [draft])), \
                patch_service("edit_listing", return_value=(200, {"id": 42, "status": "DRAFT"})) as api:
            response = self.client.post(self.url, {**ITEM, "category": category})
        return response, api

    def test_an_older_typed_category_is_shown_and_can_stay(self):
        old = {**DRAFT_LISTING, "category": "croissant stuff"}
        with patch_service("my_listings", return_value=(200, [old])):
            page = self.client.get(self.url)
        self.assertContains(page, '<option value="croissant stuff" selected>croissant stuff (current)</option>', html=True)

        response, api = self.post(old, "croissant stuff")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(api.call_args.args[2]["category"], "croissant stuff")

    def test_changing_it_must_be_to_a_listed_type(self):
        response, api = self.post(DRAFT_LISTING, "Bakeries")

        self.assertContains(response, "Choose a type of food from the list.", status_code=400)
        api.assert_not_called()

    def test_the_current_type_is_selected(self):
        with patch_service("my_listings", return_value=(200, [DRAFT_LISTING])):
            page = self.client.get(self.url)

        self.assertContains(page, '<option value="Bakery" selected>Bakery</option>', html=True)
