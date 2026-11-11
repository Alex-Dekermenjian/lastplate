"""LP-37: the three steps of a restaurant's request to join LastPlate.

The rules mirror Marketplace for quick feedback on the right step;
Marketplace is the authority and checks everything again, including the
image content.
"""

import re

from django import forms

from .area_field import AreaField, area_choices

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
    """Step 2 of 3. LP-65: the area is picked from the Lebanon places list."""

    address = forms.CharField(
        label="Address", min_length=5, max_length=255,
        help_text="Where customers collect their food, for example 12 Bliss Street, Beirut.",
        widget=forms.TextInput(attrs={"autocomplete": "street-address"}),
    )
    # LP-65: no map, so the latitude/longitude boxes are gone.
    area = AreaField()
    # LP-68: this is the first branch; up to two more can be added below.
    branch_name = forms.CharField(
        label="Branch name (optional)", required=False, max_length=100,
        help_text="Customers see it after your restaurant's name. Leave empty to use your area, for example Hamra.",
    )

    def __init__(self, *args, current_area=None, **kwargs):
        super().__init__(*args, **kwargs)
        # An area typed before LP-65 stays allowed until the owner changes it.
        self.fields["area"].choices = area_choices(current_area)

    def clean(self):
        cleaned = super().clean()
        names = [(cleaned.get("branch_name") or cleaned.get("area") or "").strip().lower()]
        for number in range(1, OTHER_BRANCHES + 1):
            values = {part: (cleaned.get(f"other_{number}_{part}") or "").strip() for part in BRANCH_PARTS}
            if any(values.values()) and not all(values.values()):
                for part, value in values.items():
                    if not value:
                        self.add_error(f"other_{number}_{part}", "Fill in the name, address and area, or leave all three empty.")
            elif all(values.values()):
                if values["name"].lower() in names:
                    self.add_error(f"other_{number}_name", "Give each branch a different name.")
                names.append(values["name"].lower())
        return cleaned

    def other_groups(self):
        """The extra branches, for the template: a heading and three fields each."""
        groups = []
        for number in range(1, OTHER_BRANCHES + 1):
            fields = [self[f"other_{number}_{part}"] for part in BRANCH_PARTS]
            groups.append({
                "number": number + 1,
                "fields": fields,
                "open": any(field.value() for field in fields) or any(field.errors for field in fields),
            })
        return groups


# LP-68: up to two more branches (three in total), each all-or-nothing.
OTHER_BRANCHES = 2
BRANCH_PARTS = ("name", "address", "area")
for _number in range(1, OTHER_BRANCHES + 1):
    LocationForm.base_fields[f"other_{_number}_name"] = forms.CharField(
        label="Branch name", required=False, min_length=2, max_length=100,
        help_text="For example Achrafieh.",
    )
    LocationForm.base_fields[f"other_{_number}_address"] = forms.CharField(
        label="Street address", required=False, min_length=5, max_length=255,
    )
    LocationForm.base_fields[f"other_{_number}_area"] = AreaField(required=False)


def other_branches(values):
    """The extra branches an owner filled in, from the saved step answers."""
    branches = []
    for number in range(1, OTHER_BRANCHES + 1):
        branch = {part: (values.get(f"other_{number}_{part}") or "").strip() for part in BRANCH_PARTS}
        if all(branch.values()):
            branches.append(branch)
    return branches


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
