"""LP-65: the Area field: a dropdown of Lebanon places (lebanon_places).

Without JavaScript it is a normal grouped dropdown. static/js/area-picker.js
turns it into a search box with a list: type "ham", pick Hamra. Either way
only a place from the list can be sent.
"""

from django import forms

from .lebanon_places import grouped_choices, search_terms, spellings

PLACEHOLDER = "Choose your area"
OLD_AREA_GROUP = "Your current area"


class AreaSelect(forms.Select):
    """A grouped dropdown whose options carry their search spellings."""

    def __init__(self, attrs=None):
        super().__init__({"class": "area-picker", "data-placeholder": "Search, e.g. Hamra", **(attrs or {})})

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        if value:
            option["attrs"]["data-search"] = search_terms(str(value), spellings(str(value)))
        return option


class AreaField(forms.ChoiceField):
    widget = AreaSelect

    def __init__(self, *, current=None, **kwargs):
        kwargs.setdefault("label", "Area")
        kwargs.setdefault("help_text", "Pick your neighbourhood or town. Customers search by it.")
        kwargs.setdefault("error_messages", {
            "required": "Choose your area from the list.",
            "invalid_choice": "Choose your area from the list.",
        })
        super().__init__(choices=area_choices(current), **kwargs)


def area_choices(current=None):
    """The list, plus a restaurant's older typed area (before LP-65) so it is
    kept until the owner picks a place from the list."""
    choices = [("", PLACEHOLDER), *grouped_choices()]
    known = {value for _, group in grouped_choices() for value, _ in group}
    if current and current not in known:
        choices.insert(1, (OLD_AREA_GROUP, [(current, current)]))
    return choices
