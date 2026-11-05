from django import forms


class CustomerRegistrationForm(forms.Form):
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
    password_confirm = forms.CharField(
        label="Confirm password", strip=False, max_length=128,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("password") and cleaned.get("password") != cleaned.get("password_confirm"):
            self.add_error("password_confirm", "The passwords do not match.")
        return cleaned


class RegistrationForm(CustomerRegistrationForm):
    """Restaurant-owner sign-up: the customer fields plus the shop name."""

    shop_name = forms.CharField(
        label="Shop name", max_length=150,
        help_text="As customers will see it. You can complete the rest of your profile later.",
        widget=forms.TextInput(attrs={"autocomplete": "organization"}),
    )

    field_order = ["display_name", "shop_name", "email", "password", "password_confirm"]


class SignInForm(forms.Form):
    email = forms.EmailField(
        label="Email", max_length=254,
        widget=forms.EmailInput(attrs={"autocomplete": "email"}),
    )
    password = forms.CharField(
        label="Password", strip=False, max_length=128,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
    )


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


class ShopSetupForm(forms.Form):
    shop_name = forms.CharField(
        label="Shop name", max_length=150,
        widget=forms.TextInput(attrs={"autocomplete": "organization"}),
    )
