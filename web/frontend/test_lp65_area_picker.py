"""LP-65: a restaurant picks its area from a searchable list of Lebanon places."""

from django.test import SimpleTestCase
from django.urls import reverse

from .apply_forms import LocationForm
from .area_field import OLD_AREA_GROUP
from .forms import ShopProfileForm
from .lebanon_places import PLACE_NAMES, REGIONS, matches, normalize
from .test_accounts import patch_service
from .test_shop_profile import PROFILE, SHOP, ShopProfilePageTests


def places():
    return [(name, others) for _, group in REGIONS for name, others in group]


class PlacesListTests(SimpleTestCase):
    def test_every_place_appears_once(self):
        names = [name for name, _ in places()]
        self.assertEqual(len(names), len(set(names)))
        self.assertGreaterEqual(len(names), 100)

    def test_no_other_spelling_is_also_another_place(self):
        """Otherwise a search could not tell two places apart."""
        by_name = {normalize(name): name for name in PLACE_NAMES}
        for name, others in places():
            for other in others:
                with self.subTest(place=name, spelling=other):
                    self.assertIn(by_name.get(normalize(other), name), (name,))

    def test_beirut_comes_first(self):
        self.assertEqual(REGIONS[0][0], "Beirut")
        self.assertIn("Hamra", dict(REGIONS[0][1]))


class SearchTests(SimpleTestCase):
    def found(self, query):
        return [name for name, others in places() if matches(query, name, others)]

    def test_typing_the_start_finds_the_place(self):
        for query, place in (("ham", "Hamra"), ("HAM", "Hamra"), ("  hAmRa ", "Hamra"), ("gemm", "Gemmayzeh")):
            with self.subTest(query=query):
                self.assertIn(place, self.found(query))

    def test_other_spellings_find_the_same_place(self):
        for query, place in (
            ("ashrafieh", "Achrafieh"), ("achraf", "Achrafieh"), ("jounie", "Jounieh"),
            ("sidon", "Saida"), ("sour", "Tyre"), ("jbeil", "Byblos"), ("centre ville", "Downtown Beirut"),
        ):
            with self.subTest(query=query):
                self.assertIn(place, self.found(query))

    def test_accents_and_dashes_do_not_matter(self):
        self.assertIn("Zahle", self.found("Zahlé"))
        self.assertIn("Ain El Mreisseh", self.found("ain-el-mr"))
        self.assertEqual(normalize("Mar  Mikhael's"), "mar mikhael s")

    def test_a_word_inside_the_name_is_found(self):
        self.assertIn("Tariq El Jdideh", self.found("jdid"))
        self.assertIn("Mar Mikhael", self.found("mikh"))

    def test_middle_of_a_word_does_not_match(self):
        """Search finds the start of words, not any letters, so results stay short."""
        self.assertNotIn("Hamra", self.found("amra"))

    def test_nothing_typed_shows_everything(self):
        self.assertEqual(len(self.found("")), len(places()))

    def test_nonsense_finds_nothing(self):
        self.assertEqual(self.found("zzzz"), [])


class AreaFieldTests(SimpleTestCase):
    def form(self, area, current_area=None):
        return LocationForm({"address": "12 Bliss Street, Beirut", "area": area}, current_area=current_area)

    def test_only_a_place_from_the_list_is_accepted(self):
        self.assertTrue(self.form("Hamra").is_valid())
        for typed in ("Hamra street", "hamra", "Beirut somewhere", ""):
            with self.subTest(typed=typed):
                form = self.form(typed)
                self.assertFalse(form.is_valid())
                self.assertEqual(form.errors["area"], ["Choose your area from the list."])

    def test_an_area_typed_before_the_list_is_kept_until_changed(self):
        self.assertTrue(self.form("hamra st.", current_area="hamra st.").is_valid())
        self.assertFalse(self.form("other text", current_area="hamra st.").is_valid())

    def test_the_dropdown_is_grouped_and_searchable(self):
        html = str(LocationForm(current_area=None)["area"])

        self.assertIn('class="area-picker"', html)
        self.assertIn('<optgroup label="Beirut">', html)
        self.assertIn('<optgroup label="Bekaa">', html)
        self.assertIn('data-search="achrafieh | ashrafieh | ashrafiyeh | achrafiyeh"', html)
        self.assertIn('<option value="" selected>Choose your area</option>', html)

    def test_the_old_area_is_shown_in_its_own_group(self):
        html = str(ShopProfileForm(initial={"area": "hamra st."}, current_area="hamra st.")["area"])

        self.assertIn(f'<optgroup label="{OLD_AREA_GROUP}">', html)
        self.assertIn('<option value="hamra st." selected data-search="hamra st.">hamra st.</option>', html)


class ProfilePageAreaTests(ShopProfilePageTests):
    """Reuses the profile page's sign-in helpers; runs only the tests below."""

    def test_saving_without_touching_an_old_area_keeps_it(self):
        old = {**SHOP, "area": "hamra st."}
        self.sign_in_session()
        with patch_service("my_shop", return_value=(200, old)), \
                patch_service("update_shop", return_value=(200, old)) as update_shop:
            response = self.client.post(self.url, {**PROFILE, "area": "hamra st."})

        self.assertRedirects(response, reverse("workspace"), fetch_redirect_response=False)
        self.assertEqual(update_shop.call_args.args[1]["area"], "hamra st.")

    def test_the_page_loads_the_search_script(self):
        self.sign_in_session()
        with patch_service("my_shop", return_value=(200, SHOP)):
            page = self.client.get(self.url)

        self.assertContains(page, "js/area-picker.js")
        self.assertContains(page, 'class="area-picker"')
