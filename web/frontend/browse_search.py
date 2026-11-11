"""LP-69: customers search the food by place and by type of food.

Marketplace already returns only the offers customers may see; this module
narrows that list for one customer's search, so nothing about visibility
changes here.

- Where: anywhere in Lebanon, a whole region ("All of Beirut") or one area
  ("Hamra"). An offer's place is its branch's area (LP-68), else the
  restaurant's.
- Type of food: one of food_types.FOOD_TYPES (the same list owners pick
  from), or surprise bags (LastBags, LP-38).
"""

from urllib.parse import urlencode

from django import forms

from .area_field import AreaSelect
from .food_types import FOOD_TYPES, type_of
from .lebanon_places import REGIONS, normalize, search_terms

ANYWHERE = ""
REGION_PREFIX = "region:"

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


LASTBAGS = "lastbag"


def food_type_choices():
    return [("", "All types of food"), *[(name, name) for name in FOOD_TYPES], (LASTBAGS, "Surprise bags (LastBag)")]


class FoodSearchForm(forms.Form):
    """The search bar above the food: where, and what type of food."""

    place = forms.ChoiceField(
        label="Where", required=False, choices=place_choices,
        widget=PlaceSelect(attrs={"data-placeholder": "Search a place, e.g. Hamra"}),
    )
    type = forms.ChoiceField(label="Type of food", required=False, choices=food_type_choices)

    def search(self):
        """The cleaned search, with anything unknown dropped instead of refused."""
        self.is_valid()  # fills cleaned_data with the parts that are fine
        return {name: self.cleaned_data.get(name) or "" for name in self.fields}


def _place_matches(offer, place):
    if not place:
        return True
    area = offer["shop"].get("area") or ""
    if place.startswith(REGION_PREFIX):
        return region_of(area) == place[len(REGION_PREFIX):]
    return normalize(area) == normalize(place)


def _type_matches(offer, food_type):
    if not food_type:
        return True
    if food_type == LASTBAGS:
        return offer["is_lastbag"]
    # Offers typed before the list are matched ignoring case and spaces.
    return type_of(offer.get("category")) == food_type


def filter_offers(offers, search):
    """The offers that fit every part of the search."""
    return [
        offer for offer in offers
        if _place_matches(offer, search.get("place") or "")
        and _type_matches(offer, search.get("type") or "")
    ]


def type_label(food_type):
    return dict(food_type_choices()).get(food_type, "") if food_type else ""


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
