
from django import forms
from django.core.exceptions import ValidationError

from .models import Listing
from .validators import validate_listing_data


class ListingForm(forms.ModelForm):

    confirm_pickup_address = forms.BooleanField(
        required=False,
        label="I confirm that the pickup address is correct",
    )

    class Meta:
        model = Listing
        fields = [
            "title",
            "description",
            "category",
            "original_price",
            "discounted_price",
            "total_quantity",
            "pickup_address",
            "pickup_start",
            "pickup_end",
        ]

        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "pickup_start": forms.DateTimeInput(
                attrs={"type": "datetime-local"},
                format="%Y-%m-%dT%H:%M",
            ),
            "pickup_end": forms.DateTimeInput(
                attrs={"type": "datetime-local"},
                format="%Y-%m-%dT%H:%M",
            ),
        }

    
    def __init__(self, *args, publishing=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.publishing = publishing

        if not publishing:
            for field in self.fields.values():
                field.required = False

        for name in ("pickup_start", "pickup_end"):
            self.fields[name].input_formats = [
                "%Y-%m-%dT%H:%M",
                "%Y-%m-%d %H:%M:%S",
            ]


    
    def clean(self):
        cleaned_data = super().clean()

        if self.publishing:
            if not cleaned_data.get("confirm_pickup_address"):
                self.add_error(
                    "confirm_pickup_address",
                    "Confirm the pickup address before publishing.",
                )

            for name in (
                "title",
                "description",
                "category",
                "pickup_address",
            ):
                if not cleaned_data.get(name):
                    self.add_error(name, "This field is required.")
        # Do not run further checks on fields that
        # already failed Django's built-in validation.
        validation_fields = (
            "original_price",
            "discounted_price",
            "total_quantity",
            "pickup_start",
            "pickup_end",
        )

        if any(name in self.errors for name in validation_fields):
            return cleaned_data

        try:
            validate_listing_data(
                original_price=cleaned_data.get("original_price"),
                discounted_price=cleaned_data.get("discounted_price"),
                stock=cleaned_data.get("total_quantity"),
                pickup_start=cleaned_data.get("pickup_start"),
                pickup_end=cleaned_data.get("pickup_end"),
                publishing=self.publishing,
            )
        except ValidationError as exc:
            for field, messages in exc.message_dict.items():
                for message in messages:
                    self.add_error(field, message)

        return cleaned_data

