
from rest_framework import serializers

from .models import Listing
from .validators import validate_listing_data


class ListingSerializer(serializers.ModelSerializer):

    class Meta:
        model = Listing
        fields = [
            "id",
            "title",
            "description",
            "category",
            "original_price",
            "discounted_price",
            "total_quantity",
            "pickup_address",
            "pickup_start",
            "pickup_end",
            "status",
            "created_at",
            "updated_at",
        ]

        read_only_fields = [
            "id",
            "status",
            "created_at",
            "updated_at",
        ]

    def validate(self, attrs):
        data = {}

        fields = [
            "original_price",
            "discounted_price",
            "total_quantity",
            "pickup_start",
            "pickup_end",
        ]

        for field in fields:
            if field in attrs:
                data[field] = attrs[field]
            elif self.instance is not None:
                data[field] = getattr(self.instance, field)
            else:
                data[field] = None

        from django.core.exceptions import ValidationError

        try:
            validate_listing_data(
                original_price=data["original_price"],
                discounted_price=data["discounted_price"],
                stock=data["total_quantity"],
                pickup_start=data["pickup_start"],
                pickup_end=data["pickup_end"],
                publishing=False,
            )
        except ValidationError as exc:
            raise serializers.ValidationError(
                exc.message_dict
            )

        return attrs
