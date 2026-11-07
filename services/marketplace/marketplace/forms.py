
from django import forms
from django.core.exceptions import ValidationError

from .models import Listing
from .validators import ITEM, required_for_publication, validate_listing_data


class ListingForm(forms.ModelForm):

    confirm_pickup_address = forms.BooleanField(
        required=False,
        label="I confirm that the pickup address is correct",
    )

    class Meta:
        model = Listing
        fields = [
            "kind",
            "title",
            "description",
            "category",
            "typical_contents",
            "allergens",
            "original_price",
            "discounted_price",
            "total_quantity",
            "pickup_address",
            "pickup_start",
            "pickup_end",
        ]

        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "typical_contents": forms.Textarea(attrs={"rows": 3}),
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

        # Which fields are required depends on the kind of offer, so
        # clean() checks them (LP-38).
        for field in self.fields.values():
            field.required = False

        # LP-38: Editing a draft keeps its kind.
        if self.instance.pk is not None:
            self.fields["kind"].disabled = True

        for name in ("pickup_start", "pickup_end"):
            self.fields[name].input_formats = [
                "%Y-%m-%dT%H:%M",
                "%Y-%m-%d %H:%M:%S",
            ]


    
    def clean(self):
        cleaned_data = super().clean()
        kind = cleaned_data.get("kind") or ITEM

        if self.publishing:
            if not cleaned_data.get("confirm_pickup_address"):
                self.add_error(
                    "confirm_pickup_address",
                    "Confirm the pickup address before publishing.",
                )

            # A LastBag's title is filled in by the server when it is saved.
            missing = required_for_publication(kind, cleaned_data.get)
            if kind != ITEM:
                missing.pop("title", None)

            for name, message in missing.items():
                self.add_error(name, message)
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
                kind=kind,
            )
        except ValidationError as exc:
            for field, messages in exc.message_dict.items():
                for message in messages:
                    self.add_error(field, message)

        return cleaned_data

