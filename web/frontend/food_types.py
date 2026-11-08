"""The types of food an offer can be, written one way everywhere.

Owners pick one from this list when they post food, and customers search
by it, so "bakery", "Bakery" and "BAKERY " are always the same type.
Offers saved before the list (typed by hand) are matched to it ignoring
case and spaces; anything else counts as "Other".
"""

FOOD_TYPES = [
    "Bakery",
    "Pastry & desserts",
    "Lebanese",
    "Grills & shawarma",
    "Sandwiches & wraps",
    "Pizza",
    "Burgers",
    "Sushi & Asian",
    "Pasta & Italian",
    "Salads & healthy",
    "Coffee & drinks",
    "Groceries",
    "Other",
]

OTHER = "Other"


def _key(value):
    return " ".join((value or "").split()).casefold()


_BY_KEY = {_key(name): name for name in FOOD_TYPES}


def canonical(value):
    """The list's spelling of a type ("bakery " -> "Bakery"), or None."""
    return _BY_KEY.get(_key(value))


def type_of(category):
    """The type an offer counts as: its listed type, "Other" for a typed
    category that isn't in the list, or "" when it has none."""
    if not _key(category):
        return ""
    return canonical(category) or OTHER


def type_choices(current=None):
    """The list for a dropdown, plus a draft's older typed category so it
    is kept until the owner picks a type from the list."""
    choices = [(name, name) for name in FOOD_TYPES]
    if current and not canonical(current):
        choices.insert(0, (current, f"{current} (current)"))
    return choices
