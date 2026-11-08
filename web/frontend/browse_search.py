"""LP-69: customers search the food by place, by words and by kind.

Marketplace already returns only the offers customers may see; this module
narrows that list for one customer's search, so nothing about visibility
changes here.

- Place: anywhere in Lebanon, a whole region ("All of Beirut") or one area
  ("Hamra"). An offer's place is its branch's area (LP-68), else the
  restaurant's.
- Words: every word must appear in the offer's title, description,
  category, typical contents, restaurant, branch or area, ignoring case and
  accents, so "croissant hamra" finds croissants in Hamra.
- Kind: everything, specific items or LastBags (LP-38).
- Category: one of the categories in today's offers.
"""

from urllib.parse import urlencode

from django import forms

from .area_field import AreaSelect
from .lebanon_places import REGIONS, normalize, search_terms

ANYWHERE = ""
REGION_PREFIX = "region:"

KIND_CHOICES = [
    ("", "All food"),
    ("ITEM", "Specific items"),
    ("LASTBAG", "LastBags (surprise)"),
]


def region_of(area):
    """The region a listed place is in, for example "Beirut" for Hamra."""
    wanted = normalize(area)
    for region, places in REGIONS:
        for name, _ in places:
            if normalize(name) == wanted:
                return region
    return ""


def place_choices():
    """Anywhere, then each region with an "All of …" choice and its places."""
    choices = [(ANYWHERE, "Anywhere in Lebanon")]
    for region, places in REGIONS:
        choices.append((region, [
            (f"{REGION_PREFIX}{region}", f"All of {region}"),
            *[(name, name) for name, _ in places],
        ]))
    return choices


class PlaceSelect(AreaSelect):
    """The LP-65 area search box; "All of Beirut" is found by "beirut" too."""

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        if str(value).startswith(REGION_PREFIX):
            region = str(value)[len(REGION_PREFIX):]
            option["attrs"]["data-search"] = search_terms(f"All of {region}", [region])
        return option


class SearchInput(forms.TextInput):
    input_type = "search"


class FoodSearchForm(forms.Form):
    """The search bar above the food. Every part is optional."""

    place = forms.ChoiceField(
        label="Where", required=False, choices=place_choices,
        widget=PlaceSelect(attrs={"data-placeholder": "Search a place, e.g. Hamra"}),
    )
    q = forms.CharField(
        label="What", required=False, max_length=80,
        widget=SearchInput(attrs={"placeholder": "e.g. croissant, pizza, bakery"}),
    )
    kind = forms.ChoiceField(label="Type", required=False, choices=KIND_CHOICES)
    category = forms.ChoiceField(label="Category", required=False, choices=[("", "All categories")])

    def __init__(self, data=None, *, categories=()):
        super().__init__(data)
        self.fields["category"].choices = [("", "All categories"), *[(name, name) for name in categories]]

    def search(self):
        """The cleaned search, with anything unknown dropped instead of refused."""
        self.is_valid()  # fills cleaned_data with the parts that are fine
        return {name: self.cleaned_data.get(name) or "" for name in self.fields}


def categories_of(offers):
    return sorted({offer["category"] for offer in offers if offer.get("category")}, key=str.lower)


def _place_matches(offer, place):
    if not place:
        return True
    area = offer["shop"].get("area") or ""
    if place.startswith(REGION_PREFIX):
        return region_of(area) == place[len(REGION_PREFIX):]
    return normalize(area) == normalize(place)


def _words_of(offer):
    parts = (
        offer.get("title"), offer.get("description"), offer.get("category"),
        offer.get("typical_contents"), offer["shop"].get("name"), offer["shop"].get("area"),
        offer.get("branch", {}).get("name"),
    )
    return normalize(" ".join(part for part in parts if part))


def _words_match(offer, query):
    words = normalize(query).split()
    if not words:
        return True
    text = _words_of(offer)
    return all(word in text for word in words)


def filter_offers(offers, search):
    """The offers that fit every part of the search."""
    kind = search.get("kind") or ""
    category = search.get("category") or ""
    return [
        offer for offer in offers
        if _place_matches(offer, search.get("place") or "")
        and _words_match(offer, search.get("q") or "")
        and (not kind or (kind == "LASTBAG") == offer["is_lastbag"])
        and (not category or offer.get("category") == category)
    ]


def place_label(place):
    if not place:
        return ""
    if place.startswith(REGION_PREFIX):
        return f"all of {place[len(REGION_PREFIX):]}"
    return place


def nearby_suggestion(offers, search):
    """When one area has nothing, how many offers the same search finds in
    the rest of its region, for a "try all of Beirut" link."""
    place = search.get("place") or ""
    if not place or place.startswith(REGION_PREFIX):
        return None
    region = region_of(place)
    if not region:
        return None
    wider = filter_offers(offers, {**search, "place": f"{REGION_PREFIX}{region}"})
    if not wider:
        return None
    wider_search = {**search, "place": f"{REGION_PREFIX}{region}"}
    return {
        "region": region,
        "count": len(wider),
        "query": urlencode({name: value for name, value in wider_search.items() if value}),
    }
