"""LP-38: Restaurants create LastBags (surprise bags); customers see them."""

from html import unescape

from django.test import TestCase
from django.urls import reverse
from django.utils.html import strip_tags

from .test_accounts import ApprovedShopMixin, WebAuthTestCase, patch_service
from .test_browse_offers import offer
from .test_shop_profile import COMPLETE_SHOP


def page_text(response):
    """The page's visible text with whitespace collapsed."""
    return " ".join(unescape(strip_tags(response.content.decode())).split())


BAG_POST = {
    "kind": "LASTBAG",
    "typical_contents": "Croissants, man'oushe, a loaf of bread",
    "original_price": "15",
    "discounted_price": "5",
    "allergens": "Gluten, milk, sesame",
    "total_quantity": "3",
    "pickup_address": "12 Bliss Street, Beirut",
    "pickup_start": "2026-11-07T19:00",
    "pickup_end": "2026-11-07T20:00",
}

DRAFT_BAG = {
    "id": 42,
    "kind": "LASTBAG",
    "title": "Bakery surprise bag",
    "description": "",
    "category": "",
    "typical_contents": "Croissants and bread",
    "status": "DRAFT",
    "original_price": "15.00",
    "discounted_price": "5.00",
    "total_quantity": 3,
    "pickup_address": "12 Bliss Street, Beirut",
    "pickup_start": None,
    "pickup_end": None,
    "ingredients": "",
    "allergens": "Gluten",
    "photo_display": None,
}


def bag_offer(**overrides):
    data = {
        "id": 7,
        "kind": "LASTBAG",
        "title": "Bakery surprise bag",
        "description": "",
        "typical_contents": "Croissants, man'oushe, a loaf of bread",
        "ingredients_display": "Changes daily",
        "allergens_display": "Gluten, milk, sesame",
        "original_price": "15.00",
        "discounted_price": "5.00",
        **overrides,
    }
    return offer(**data)


class NewOfferFormTests(ApprovedShopMixin, WebAuthTestCase):

    def setUp(self):
        self.url = reverse("create-listing")
        self.sign_in_session()

    def test_form_starts_with_the_choice(self):
        response = self.client.get(self.url)

        self.assertContains(response, "LastBag (surprise)")
        self.assertContains(response, "Specific item")
        self.assertContains(response, f'href="{self.url}?kind=LASTBAG"')

    def test_lastbag_form_shows_the_bag_fields(self):
        response = self.client.get(self.url, {"kind": "LASTBAG"})

        for label in ("What's usually inside", "Worth ($)", "Your price ($)",
                      "May contain (allergens)", "Quantity", "Pickup starts", "Add photo (optional)"):
            self.assertContains(response, label)
        self.assertContains(response, 'name="kind" value="LASTBAG"')
        # No exact ingredients, title, description or category for a bag.
        for name in ("ingredients", "title", "description", "category"):
            self.assertNotContains(response, f'name="{name}"')

    def test_specific_item_form_keeps_todays_fields(self):
        response = self.client.get(self.url, {"kind": "ITEM"})

        for name in ("title", "description", "category", "ingredients", "allergens"):
            self.assertContains(response, f'name="{name}"')
        self.assertNotContains(response, 'name="typical_contents"')
        self.assertNotContains(response, 'name="kind"')

    def test_lastbag_is_saved_as_a_lastbag(self):
        with patch_service("create_listing", return_value=(201, {"id": 7})) as create:
            response = self.client.post(self.url, BAG_POST)

        self.assertRedirects(response, reverse("workspace"), fetch_redirect_response=False)
        create.assert_called_once_with("access-token-1", BAG_POST)

    def test_lastbag_errors_use_the_bag_labels(self):
        refusal = {"discounted_price": ["Your price must be less than the bag's worth."]}

        with patch_service("create_listing", return_value=(400, refusal)):
            response = self.client.post(self.url, {**BAG_POST, "discounted_price": "15"})

        self.assertEqual(response.status_code, 400)
        self.assertIn("Your price: Your price must be less than the bag's worth.", page_text(response))
        # The bag form is shown again with what was typed.
        self.assertContains(response, "Croissants, man&#x27;oushe", status_code=400)
        self.assertContains(response, 'name="kind" value="LASTBAG"', status_code=400)


class EditLastBagTests(ApprovedShopMixin, WebAuthTestCase):

    def setUp(self):
        self.url = reverse("edit-listing", args=[42])
        self.sign_in_session()

    def test_editing_a_lastbag_keeps_the_bag_form_without_a_choice(self):
        with patch_service("my_listings", return_value=(200, [DRAFT_BAG])):
            response = self.client.get(self.url)

        self.assertContains(response, "LastBag · surprise")
        self.assertContains(response, "Croissants and bread")
        self.assertNotContains(response, "?kind=ITEM")
        self.assertNotContains(response, 'name="kind"')

    def test_saving_a_lastbag_sends_bag_fields_but_never_the_kind(self):
        with patch_service("my_listings", return_value=(200, [DRAFT_BAG])), \
                patch_service("edit_listing", return_value=(200, DRAFT_BAG)) as edit:
            self.client.post(self.url, {**BAG_POST, "kind": "ITEM", "title": "Ignored"})

        sent = edit.call_args.args[2]
        self.assertEqual(sent["typical_contents"], BAG_POST["typical_contents"])
        self.assertNotIn("kind", sent)
        self.assertNotIn("title", sent)


class PublishLastBagTests(ApprovedShopMixin, WebAuthTestCase):

    def test_publish_refusal_names_the_missing_bag_fields(self):
        self.sign_in_session()
        refusal = {"fields": {"typical_contents": ["Describe what's usually inside the bag."]}}

        with patch_service("publish_listing", return_value=(400, refusal)):
            response = self.client.post(reverse("publish-listing", args=[42]))

        messages = [str(m) for m in response.wsgi_request._messages]
        self.assertIn(
            "What's usually inside: Describe what's usually inside the bag",
            " ".join(messages),
        )


class WorkspaceLastBagTests(WebAuthTestCase):

    def test_workspace_labels_the_lastbag(self):
        self.sign_in_session()
        published = {**DRAFT_BAG, "status": "PUBLISHED", "free_quantity": 3, "ingredients_display": "Unknown"}

        with patch_service("my_shop", return_value=(200, COMPLETE_SHOP)), \
                patch_service("my_listings", return_value=(200, [published])):
            response = self.client.get(reverse("workspace"))

        text = page_text(response)
        self.assertIn("LastBag · surprise", text)
        self.assertIn("What's usually inside: Croissants and bread", text)
        self.assertIn("Worth: $15.00", text)
        self.assertIn("Your price: $5.00", text)
        self.assertNotIn("Ingredients:", text)


class CustomerLastBagTests(TestCase):

    def test_offers_page_shows_label_worth_price_and_typical_contents(self):
        with patch_service("browse_listings", return_value=(200, [bag_offer(), offer()])):
            response = self.client.get(reverse("browse-offers"))

        text = page_text(response)
        self.assertIn("LastBag · surprise", text)
        self.assertIn("Worth $15.00, yours for $5.00", text)
        self.assertIn("Usually: Croissants, man'oushe, a loaf of bread. Contents change daily.", text)
        self.assertIn("May contain Gluten, milk, sesame", text)
        # The specific item next to it is shown as before.
        self.assertIn("Croissant Box", text)
        self.assertEqual(text.count("LastBag · surprise"), 1)

    def test_offer_page_shows_typical_contents_never_exact_contents(self):
        data = bag_offer(description="Exactly 2 croissants", ingredients_display="Flour, butter")

        with patch_service("listing_offer", return_value=(200, data)):
            response = self.client.get(reverse("offer-detail", args=[7]))

        text = page_text(response)
        self.assertIn("LastBag · surprise", text)
        self.assertIn("Worth $15.00, yours for $5.00", text)
        self.assertIn("Usually: Croissants, man'oushe, a loaf of bread", text)
        self.assertIn("May contain: Gluten, milk, sesame", text)
        self.assertNotIn("Exactly 2 croissants", text)
        self.assertNotIn("Flour, butter", text)
        self.assertNotIn("Ingredients", text)

    def test_item_offer_page_is_unchanged(self):
        with patch_service("listing_offer", return_value=(200, offer())):
            response = self.client.get(reverse("offer-detail", args=[42]))

        text = page_text(response)
        self.assertNotIn("LastBag", text)
        self.assertIn("Ingredients Flour, butter", text)
