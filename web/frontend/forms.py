from django import forms


# LP-56: the only account types anyone can sign up for. There is no admin or
# staff choice; any other value fails validation.
ACCOUNT_TYPES = [
    ("customer", "I want to buy food"),
    ("restaurant", "I own a restaurant"),
]


class SignUpForm(forms.Form):
    account_type = forms.ChoiceField(
        label="How will you use LastPlate?", choices=ACCOUNT_TYPES, widget=forms.RadioSelect,
        error_messages={"required": "Choose how you will use LastPlate."},
    )
    display_name = forms.CharField(
        label="Your name", max_length=150,
        widget=forms.TextInput(attrs={"autocomplete": "name"}),
    )
    email = forms.EmailField(
        label="Email", max_length=254,
        widget=forms.EmailInput(attrs={"autocomplete": "email"}),
    )
    password = forms.CharField(
        label="Password", strip=False, max_length=128,
        help_text="At least 8 characters. Avoid common passwords, numbers only, or your name or email.",
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )


class LoginForm(forms.Form):
    email = forms.EmailField(
        label="Email", max_length=254,
        widget=forms.EmailInput(attrs={"autocomplete": "email", "placeholder": "you@example.com"}),
    )
    password = forms.CharField(
        label="Password", strip=False, max_length=128,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
    )
    remember_me = forms.BooleanField(label="Keep me logged in", required=False, initial=True)


class ShopProfileForm(forms.Form):
    """Length rules mirror Marketplace for quick feedback; Marketplace is the
    authority and its errors (including phone-number checks) are shown too."""

    name = forms.CharField(
        label="Shop name", min_length=2, max_length=150,
        widget=forms.TextInput(attrs={"autocomplete": "organization"}),
    )
    address = forms.CharField(
        label="Pickup address", min_length=5, max_length=255,
        help_text="Where customers collect their food.",
        widget=forms.TextInput(attrs={"autocomplete": "street-address"}),
    )
    area = forms.CharField(
        label="Area", min_length=2, max_length=100,
        help_text="Neighbourhood customers can filter by, for example Hamra.",
        widget=forms.TextInput(attrs={"autocomplete": "address-level2"}),
    )
    public_contact = forms.CharField(
        label="Shop phone number", max_length=32,
        help_text="Shown to customers so they can call the shop, for example +961 1 234 567. "
                  "Your sign-in email stays private and is never shown.",
        widget=forms.TextInput(attrs={"type": "tel", "inputmode": "tel", "autocomplete": "off"}),
    )

