
import re

from django.core.exceptions import ValidationError
from rest_framework import serializers

from .image_validators import validate_listing_photo
from .models import Listing, Shop, StockAdjustment
from .stock import free_quantity as calculate_free_quantity
from .validators import validate_listing_data


# LP-8: Public contact must be a valid phone number.
PHONE_CHARACTERS = re.compile(r"\+?[0-9 ()./-]+")


def validate_public_contact(value):
    if not PHONE_CHARACTERS.fullmatch(value):
        raise serializers.ValidationError(
            "Enter a phone number using digits, spaces, +, -, "
            "brackets or dots, for example +961 1 234 567."
        )

    digits = re.sub(r"\D", "", value)

    if not 7 <= len(digits) <= 15:
        raise serializers.ValidationError(
            "Enter a full phone number with 7 to 15 digits, "
            "for example +961 1 234 567."
        )

    if len(set(digits)) == 1:
        raise serializers.ValidationError(
            "Enter a real phone number customers can call."
        )

    return value


# LP-7 and LP-8: Reject protected or unknown input fields.
class RejectUnknownFieldsMixin:
    def to_internal_value(self, data):
        unexpected = sorted(set(data) - set(self.fields))

        if unexpected:
            raise serializers.ValidationError(
                {
                    name: ["This field is not allowed."]
                    for name in unexpected
                }
            )

        return super().to_internal_value(data)


# LP-7: Shop creation
class ShopCreateSerializer(
    RejectUnknownFieldsMixin,
    serializers.Serializer,
):
    """
    Only the shop name can be supplied during creation.
    Ownership and approval come from the server.
    """

    name = serializers.CharField(max_length=150)


# LP-8: Shop profile editing
class ShopProfileSerializer(
    RejectUnknownFieldsMixin,
    serializers.Serializer,
):
    """
    Shop operators may update profile information,
    but cannot change approval or ownership fields.
    """

    name = serializers.CharField(
        min_length=2,
        max_length=150,
        required=False,
    )

    address = serializers.CharField(
        min_length=5,
        max_length=255,
        required=False,
    )

    area = serializers.CharField(
        min_length=2,
        max_length=100,
        required=False,
    )

    public_contact = serializers.CharField(
        max_length=32,
        required=False,
        validators=[validate_public_contact],
    )

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError(
                "Send at least one profile field to change."
            )

        return attrs


# LP-7 and LP-8: Shop details
class ShopSerializer(serializers.ModelSerializer):

    profile_complete = serializers.BooleanField(
        read_only=True
    )

    class Meta:
        model = Shop

        fields = [
            "id",
            "operator_user_id",
            "name",
            "address",
            "area",
            "public_contact",
            "approval_status",
            "rejection_reason",
            "profile_complete",
            "created_at",
            "updated_at",
        ]

        read_only_fields = fields


# LP-11: How optional listing details are shown. Missing ingredient or
# allergen information displays "Unknown".
class ListingDisplayMixin(serializers.Serializer):

    ingredients_display = serializers.SerializerMethodField()
    allergens_display = serializers.SerializerMethodField()
    photo_display = serializers.SerializerMethodField()

    # LP-12: Units not committed to reservations.
    free_quantity = serializers.SerializerMethodField()

    def get_free_quantity(self, obj):
        return calculate_free_quantity(obj)

    def get_ingredients_display(self, obj):
        if obj.ingredients and obj.ingredients.strip():
            return obj.ingredients.strip()
        return "Unknown"

    def get_allergens_display(self, obj):
        if obj.allergens and obj.allergens.strip():
            return obj.allergens.strip()
        return "Unknown"

    def get_photo_display(self, obj):
        if obj.photo:
            return obj.photo.url
        return None


# LP-10 and LP-11: Food listings (shop operator view)
class ListingSerializer(ListingDisplayMixin, serializers.ModelSerializer):

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
            "free_quantity",
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

    # LP-14: Reject, rather than silently drop, unknown and read-only fields
    # (for example terms_revision, first_reserved_at or free_quantity).
    def to_internal_value(self, data):
        writable = {
            name for name, field in self.fields.items()
            if not field.read_only
        }
        unexpected = sorted(set(data) - writable)

        if unexpected:
            raise serializers.ValidationError(
                {name: ["This field is not allowed."] for name in unexpected}
            )

        return super().to_internal_value(data)

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


# LP-13: Public marketplace. Only what customers need; never the operator's
# user ID or sign-in email (the Shop model does not store the email at all).
class PublicShopSerializer(serializers.ModelSerializer):

    class Meta:
        model = Shop
        fields = ["name", "area", "public_contact"]
        read_only_fields = fields


class PublicListingSerializer(ListingDisplayMixin, serializers.ModelSerializer):
    """
    A browsable offer. The view passes the already-loaded shops in
    context["shops"] ({shop_id: Shop}), so no query runs per listing.
    """

    shop = serializers.SerializerMethodField()

    def get_shop(self, obj):
        shop = self.context["shops"][obj.shop_id]
        return PublicShopSerializer(shop).data

    class Meta:
        model = Listing
        fields = [
            "id",
            "title",
            "description",
            "category",
            "photo_display",
            "ingredients_display",
            "allergens_display",
            "original_price",
            "discounted_price",
            "free_quantity",
            "pickup_address",
            "pickup_start",
            "pickup_end",
            "shop",
        ]
        read_only_fields = fields


class PublicListingDetailSerializer(PublicListingSerializer):
    """One offer, plus the terms revision a reservation must quote."""

    class Meta(PublicListingSerializer.Meta):
        fields = [*PublicListingSerializer.Meta.fields, "terms_revision"]
        read_only_fields = fields


# LP-12: Stock adjustment request and history
class StockAdjustmentRequestSerializer(
    RejectUnknownFieldsMixin,
    serializers.Serializer,
):
    """
    Only the change and its reason can be supplied.
    Listing, actor and kind come from the server.
    """

    quantity_change = serializers.IntegerField()
    reason = serializers.CharField(max_length=500)


class StockAdjustmentSerializer(serializers.ModelSerializer):

    class Meta:
        model = StockAdjustment

        fields = [
            "id",
            "listing",
            "quantity_change",
            "reason",
            "kind",
            "reservation_ref",
            "actor_user_id",
            "created_at",
        ]

        read_only_fields = fields
