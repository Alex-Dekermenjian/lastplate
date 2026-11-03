from django import forms


class RegistrationForm(forms.Form):
    display_name = forms.CharField(
        label="Your name", max_length=150,
        widget=forms.TextInput(attrs={"autocomplete": "name"}),
    )
    shop_name = forms.CharField(
        label="Shop name", max_length=150,
        help_text="As customers will see it. You can complete the rest of your profile later.",
        widget=forms.TextInput(attrs={"autocomplete": "organization"}),
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


class SignInForm(forms.Form):
    email = forms.EmailField(
        label="Email", max_length=254,
        widget=forms.EmailInput(attrs={"autocomplete": "email"}),
    )
    password = forms.CharField(
        label="Password", strip=False, max_length=128,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
    )


class ShopSetupForm(forms.Form):
    shop_name = forms.CharField(
        label="Shop name", max_length=150,
        widget=forms.TextInput(attrs={"autocomplete": "organization"}),
    )
