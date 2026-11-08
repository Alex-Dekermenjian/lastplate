"""LP-37: the three steps of a restaurant's request to join LastPlate.

The rules mirror Marketplace for quick feedback on the right step;
Marketplace is the authority and checks everything again, including the
image content.
"""

import re

from django import forms

# The stored value is the readable word itself (Marketplace Shop.category).
CATEGORIES = ["Bakery", "Café", "Restaurant", "Sushi", "Pastry", "Grocery", "Other"]

MAX_IMAGE_SIZE = 5 * 1024 * 1024
IMAGE_TYPES = {
    "jpg": b"\xff\xd8\xff",
    "jpeg": b"\xff\xd8\xff",
    "png": b"\x89PNG\r\n\x1a\n",
}
BAD_IMAGE = "Choose a JPEG or PNG image."
PHONE_CHARACTERS = re.compile(r"\+?[0-9 ()./-]+")


def check_phone(value):
    """The Marketplace phone rule (LP-8), so mistakes show on step 1."""
    digits = re.sub(r"\D", "", value)
    if not PHONE_CHARACTERS.fullmatch(value):
        raise forms.ValidationError(
            "Enter a phone number using digits, spaces, +, -, brackets or dots, for example +961 1 234 567."
        )
    if not 7 <= len(digits) <= 15:
        raise forms.ValidationError("Enter a full phone number with 7 to 15 digits, for example +961 1 234 567.")
    if len(set(digits)) == 1:
        raise forms.ValidationError("Enter a real phone number customers can call.")


def check_image(uploaded):
    """A quick check of size, name and the file's first bytes."""
    if uploaded.size > MAX_IMAGE_SIZE:
        raise forms.ValidationError("Image must not exceed 5 MB.")
    if uploaded.size == 0:
        raise forms.ValidationError("Image cannot be empty.")

    extension = uploaded.name.rsplit(".", 1)[-1].lower() if "." in uploaded.name else ""
    signature = IMAGE_TYPES.get(extension)
    uploaded.seek(0)
    start = uploaded.read(8)
    uploaded.seek(0)
    if signature is None or not start.startswith(signature):
        raise forms.ValidationError(BAD_IMAGE)


class RestaurantInfoForm(forms.Form):
    """Step 1 of 3."""

    name = forms.CharField(
        label="Restaurant name", min_length=2, max_length=150,
        widget=forms.TextInput(attrs={"autocomplete": "organization"}),
    )
    category = forms.ChoiceField(
        label="Category",
        choices=[("", "Choose a category"), *((value, value) for value in CATEGORIES)],
        error_messages={"required": "Choose a category.", "invalid_choice": "Choose a category from the list."},
    )
    public_contact = forms.CharField(
        label="Restaurant phone", max_length=32, validators=[check_phone],
        help_text="Shown to customers so they can call you.",
        widget=forms.TextInput(attrs={"type": "tel", "inputmode": "tel", "autocomplete": "tel"}),
    )


class LocationForm(forms.Form):
    """Step 2 of 3. The map position is optional until a map pin exists."""

    address = forms.CharField(
        label="Address", min_length=5, max_length=255,
        help_text="Where customers collect their food, for example 12 Bliss Street, Beirut.",
        widget=forms.TextInput(attrs={"autocomplete": "street-address"}),
    )
    area = forms.CharField(
        label="Area", min_length=2, max_length=100,
        help_text="Neighbourhood customers can filter by, for example Hamra.",
        widget=forms.TextInput(attrs={"autocomplete": "address-level2"}),
    )
    latitude = forms.DecimalField(
        label="Latitude (optional)", required=False, min_value=-90, max_value=90, max_digits=9, decimal_places=6,
        widget=forms.TextInput(attrs={"inputmode": "decimal"}),
    )
    longitude = forms.DecimalField(
        label="Longitude (optional)", required=False, min_value=-180, max_value=180, max_digits=9, decimal_places=6,
        widget=forms.TextInput(attrs={"inputmode": "decimal"}),
    )

    def clean(self):
        cleaned = super().clean()
        latitude, longitude = cleaned.get("latitude"), cleaned.get("longitude")
        if not self.errors and (latitude is None) != (longitude is None):
            self.add_error("longitude", "Enter both latitude and longitude, or leave both empty.")
        return cleaned


class PhotosForm(forms.Form):
    """Step 3 of 3. held: images kept from an earlier try, {field: filename}."""

    photo = forms.FileField(
        label="Cover photo", required=False,
        help_text="A photo of your restaurant or your food. JPEG or PNG, up to 5 MB.",
        widget=forms.ClearableFileInput(attrs={"accept": "image/jpeg,image/png"}),
    )
    logo = forms.FileField(
        label="Logo (optional)", required=False,
        # LP-66: logos are shown in a small circle, where long word logos are hard to read.
        help_text="Shown in a small circle. A square logo or app icon works best. JPEG or PNG, up to 5 MB.",
        widget=forms.ClearableFileInput(attrs={"accept": "image/jpeg,image/png"}),
    )
    accept_conditions = forms.BooleanField(
        label="I accept LastPlate's conditions for restaurants",
        error_messages={"required": "Tick the box to accept LastPlate's conditions for restaurants."},
    )

    def __init__(self, *args, held=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.held = held or {}

    def clean_photo(self):
        photo = self.cleaned_data.get("photo")
        if photo:
            check_image(photo)
        elif "photo" not in self.held:
            raise forms.ValidationError("Add a cover photo of your restaurant.")
        return photo

    def clean_logo(self):
        logo = self.cleaned_data.get("logo")
        if logo:
            check_image(logo)
        return logo
