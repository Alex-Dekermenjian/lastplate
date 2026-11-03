
from rest_framework import serializers

from .models import Listing
from .validators import validate_listing_data
from .image_validators import validate_listing_photo
from django.core.exceptions import ValidationError


class ListingSerializer(serializers.ModelSerializer):
    
    ingredients_display = serializers.SerializerMethodField()
    allergens_display = serializers.SerializerMethodField()
    photo_display = serializers.SerializerMethodField()
    def get_ingredients_display(self, obj):
        return obj.ingredients.strip() if obj.ingredients and obj.ingredients.strip() else "Unknown"

    def get_allergens_display(self, obj):
        return obj.allergens.strip() if obj.allergens and obj.allergens.strip() else "Unknown"
    
    def get_photo_display(self, obj):
        if obj.photo:
            return obj.photo.url

        return None


    class Meta:
        model = Listing
        
        fields = [
            "id",
            "title",
            "description",
            "category",
            "photo",
            "photo_display",
            "ingredients",
            "allergens",
            "ingredients_display",
            "allergens_display",
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
    
    def validate_photo(self, value):
        if value:
            try:
                validate_listing_photo(value)
            except ValidationError as exc:
                raise serializers.ValidationError(exc.messages)
        return value


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
