"""LP-69: the home page shows the food, and customers search it by place,
by words, by type and by category."""

from unittest import mock
from urllib.error import URLError

from django.test import TestCase
from django.urls import reverse

from .browse_search import filter_offers, place_choices, region_of
from .browse_views import offer_for_display
from .services import ServiceUnavailable
from .test_accounts import patch_service
from .test_browse_offers import UNAVAILABLE, offer

HAMRA_CROISSANTS = offer(id=1, title="Butter Croissants", category="Bakery",
                         shop={"id": 1, "name": "Cedar Bakery", "area": "Hamra"})
ACHRAFIEH_PIZZA = offer(id=2, title="Pizza Slices", description="Cheese and za'atar", category="Restaurant",
                        shop={"id": 2, "name": "Slice Corner", "area": "Achrafieh"})
JOUNIEH_BAG = offer(id=3, kind="LASTBAG", title="Evening LastBag", typical_contents="bread and pastries",
                    category="Bakery", shop={"id": 3, "name": "Bay Bakery", "area": "Jounieh"})
BRANCH_OFFER = offer(id=4, title="Mana'eesh", category="Bakery",
                     shop={"id": 1, "name": "Cedar Bakery", "area": "Hamra", "branch_count": 2},
                     branch={"id": 9, "name": "Gemmayzeh", "area": "Gemmayzeh"})
ALL = [HAMRA_CROISSANTS, ACHRAFIEH_PIZZA, JOUNIEH_BAG, BRANCH_OFFER]


def titles(response):
    return [item["title"] for item in response.context["offers"]]


class SearchTestCase(TestCase):
    url = reverse("browse-offers")

    def search(self, **params):
        with patch_service("browse_listings", return_value=(200, ALL)):
            return self.client.get(self.url, params)


class PlaceSearchTests(SearchTestCase):

    def test_without_a_search_everything_is_shown(self):
        response = self.search()

        self.assertEqual(len(titles(response)), 4)
        self.assertContains(response, "4 offers available")
        self.assertContains(response, 'role="search"')

    def test_one_area_shows_only_its_food(self):
        response = self.search(place="Achrafieh")

        self.assertEqual(titles(response), ["Pizza Slices"])
        self.assertContains(response, "1 offer")
        self.assertContains(response, "in Achrafieh")

    def test_a_whole_region(self):
        response = self.search(place="region:Beirut")

        self.assertEqual(titles(response), ["Butter Croissants", "Pizza Slices", "Mana'eesh"])
        self.assertContains(response, "in all of Beirut")

    def test_a_branch_is_found_by_its_own_area_not_the_restaurants(self):
        self.assertEqual(titles(self.search(place="Gemmayzeh")), ["Mana'eesh"])
        self.assertNotIn("Mana'eesh", titles(self.search(place="Hamra")))

    def test_an_area_with_nothing_suggests_the_rest_of_its_region(self):
        response = self.search(place="Badaro")

        self.assertEqual(titles(response), [])
        self.assertContains(response, "No food matches your search right now.")
        self.assertContains(response, "See 3 offers in all of Beirut")
        self.assertContains(response, "?place=region%3ABeirut")
        self.assertContains(response, "See all 4 offers in Lebanon")

    def test_an_unknown_place_is_ignored_instead_of_failing(self):
        response = self.search(place="Atlantis")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(titles(response)), 4)

    def test_the_place_box_lists_regions_and_their_areas(self):
        response = self.search()

        self.assertContains(response, 'class="area-picker"')
        self.assertContains(response, "Anywhere in Lebanon")
        self.assertContains(response, '<option value="region:Beirut" data-search="all of beirut | beirut">All of Beirut</option>', html=True)
        self.assertContains(response, ">Achrafieh</option>")


class WordSearchTests(SearchTestCase):

    def test_words_match_the_title_description_restaurant_and_contents(self):
        self.assertEqual(titles(self.search(q="croissant")), ["Butter Croissants"])
        self.assertEqual(titles(self.search(q="ZA'ATAR")), ["Pizza Slices"])
        self.assertEqual(titles(self.search(q="slice corner")), ["Pizza Slices"])
        self.assertEqual(titles(self.search(q="pastries")), ["Butter Croissants", "Evening LastBag", "Mana'eesh"])

    def test_every_word_must_match(self):
        self.assertEqual(titles(self.search(q="bakery jounieh")), ["Evening LastBag"])
        self.assertEqual(titles(self.search(q="pizza jounieh")), [])

    def test_words_and_place_together(self):
        response = self.search(q="bakery", place="region:Beirut")

        self.assertEqual(titles(response), ["Butter Croissants", "Mana'eesh"])
        self.assertContains(response, "2 offers")
        self.assertContains(response, "for “bakery”")

    def test_the_words_typed_are_kept_in_the_box(self):
        self.assertContains(self.search(q="pizza"), 'value="pizza"')


class TypeAndCategoryTests(SearchTestCase):

    def test_lastbags_only_or_items_only(self):
        self.assertEqual(titles(self.search(kind="LASTBAG")), ["Evening LastBag"])
        self.assertNotIn("Evening LastBag", titles(self.search(kind="ITEM")))

    def test_category_choices_come_from_todays_food(self):
        response = self.search(category="Restaurant")

        self.assertEqual(titles(response), ["Pizza Slices"])
        self.assertContains(response, '<option value="Bakery">Bakery</option>', html=True)
        # The filters stay open when one is used.
        self.assertContains(response, '<details class="food-search__more" open>')

    def test_clear_returns_to_everything(self):
        self.assertContains(self.search(q="pizza"), 'href="?">Clear</a>')
        self.assertNotContains(self.search(), ">Clear</a>")


class HomePageFoodTests(TestCase):

    def home(self, **kwargs):
        with mock.patch("frontend.views.urlopen", side_effect=URLError("down")), \
                patch_service("browse_listings", **kwargs):
            return self.client.get(reverse("home"))

    def test_the_home_page_shows_the_food_and_the_search(self):
        response = self.home(return_value=(200, ALL))

        self.assertContains(response, "Food left near you")
        self.assertContains(response, 'role="search"')
        self.assertContains(response, "Butter Croissants")
        self.assertContains(response, "4 offers available")
        # Still links to the full page, and to sign-up for restaurants.
        self.assertContains(response, f'href="{reverse("browse-offers")}"')
        self.assertContains(response, f'href="{reverse("signup")}"')

    def test_the_home_page_searches_too(self):
        with mock.patch("frontend.views.urlopen", side_effect=URLError("down")), \
                patch_service("browse_listings", return_value=(200, ALL)):
            response = self.client.get(reverse("home"), {"place": "Jounieh"})

        self.assertEqual(titles(response), ["Evening LastBag"])

    def test_the_home_page_still_opens_when_marketplace_is_down(self):
        response = self.home(side_effect=ServiceUnavailable("marketplace"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, UNAVAILABLE)
        self.assertNotContains(response, 'role="search"')

    def test_no_food_today(self):
        response = self.home(return_value=(200, []))

        self.assertContains(response, "no food available right now.")


class PlaceHelpersTests(TestCase):

    def test_region_of_a_place(self):
        self.assertEqual(region_of("Hamra"), "Beirut")
        self.assertEqual(region_of("hamra"), "Beirut")
        self.assertEqual(region_of("Atlantis"), "")

    def test_every_region_has_an_all_of_choice(self):
        regions = [label for label, group in place_choices()[1:]]
        for region in regions:
            values = [value for value, _ in dict(place_choices()[1:])[region]]
            self.assertEqual(values[0], f"region:{region}")

    def test_an_offer_without_an_area_only_shows_when_searching_anywhere(self):
        no_area = offer_for_display(offer(id=5, shop={"id": 5, "name": "X", "area": ""}))

        self.assertEqual(filter_offers([no_area], {"place": ""}), [no_area])
        self.assertEqual(filter_offers([no_area], {"place": "Hamra"}), [])
