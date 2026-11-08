
import re

from django.core.exceptions import ValidationError
from rest_framework import serializers

from .image_validators import validate_listing_photo
from .models import Listing, Shop, StockAdjustment
from .stock import free_quantity as calculate_free_quantity
from .validators import LASTBAG as LASTBAG_KIND, validate_listing_data


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


# LP-37: Restaurant images use the LP-11 checks (JPEG or PNG, at most 5 MB,
# checked by content rather than by file name).
def validate_shop_image(value):
    try:
        validate_listing_photo(value)
    except ValidationError as exc:
        raise serializers.ValidationError(exc.messages)

    return value


# LP-37: Optional map position. A map pin can fill these in later.
COORDINATE = {
    "max_digits": 9,
    "decimal_places": 6,
    "required": False,
    "allow_null": True,
}
SEND_BOTH_COORDINATES = "Enter both latitude and longitude, or leave both empty."


def validate_coordinate_pair(attrs):
    if (attrs.get("latitude") is None) != (attrs.get("longitude") is None):
        raise serializers.ValidationError(
            {"longitude": [SEND_BOTH_COORDINATES]}
        )


def image_url(image):
    return image.url if image else None


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

    # LP-37: category, map position and images can be changed later too.
    category = serializers.ChoiceField(
        choices=Shop.Category.choices,
        required=False,
    )
    latitude = serializers.DecimalField(
        min_value=-90, max_value=90, **COORDINATE,
    )
    longitude = serializers.DecimalField(
        min_value=-180, max_value=180, **COORDINATE,
    )
    photo = serializers.FileField(
        required=False,
        validators=[validate_shop_image],
    )
    logo = serializers.FileField(
        required=False,
        validators=[validate_shop_image],
    )

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError(
                "Send at least one profile field to change."
            )

        # Coordinates change together, so a pin is never half moved.
        if ("latitude" in attrs) != ("longitude" in attrs):
            raise serializers.ValidationError(
                {"longitude": [SEND_BOTH_COORDINATES]}
            )
        validate_coordinate_pair(attrs)

        return attrs


# LP-7 and LP-8: Shop details
class ShopSerializer(serializers.ModelSerializer):

    profile_complete = serializers.BooleanField(
        read_only=True
    )
    application_sent = serializers.BooleanField(read_only=True)
    photo_display = serializers.SerializerMethodField()
    logo_display = serializers.SerializerMethodField()

    class Meta:
        model = Shop

        fields = [
            "id",
            "operator_user_id",
            "name",
            "address",
            "area",
            "public_contact",
            "category",
            "latitude",
            "longitude",
            "photo_display",
            "logo_display",
            "approval_status",
            "rejection_reason",
            "profile_complete",
            "application_sent",
            "conditions_accepted_at",
            "created_at",
            "updated_at",
        ]

        read_only_fields = fields

    def get_photo_display(self, obj):
        return image_url(obj.photo)

    def get_logo_display(self, obj):
        return image_url(obj.logo)


# LP-37: The restaurant's request to join LastPlate, sent once all three
# steps are filled in. Ownership and approval come from the server.
class ShopApplicationSerializer(
    RejectUnknownFieldsMixin,
    serializers.Serializer,
):
    # Step 1: restaurant info
    name = serializers.CharField(min_length=2, max_length=150)
    category = serializers.ChoiceField(choices=Shop.Category.choices)
    public_contact = serializers.CharField(
        max_length=32,
        validators=[validate_public_contact],
    )

    # Step 2: location
    address = serializers.CharField(min_length=5, max_length=255)
    area = serializers.CharField(min_length=2, max_length=100)
    latitude = serializers.DecimalField(
        min_value=-90, max_value=90, **COORDINATE,
    )
    longitude = serializers.DecimalField(
        min_value=-180, max_value=180, **COORDINATE,
    )

    # Step 3: photos and conditions
    photo = serializers.FileField(
        required=False,
        validators=[validate_shop_image],
    )
    logo = serializers.FileField(
        required=False,
        validators=[validate_shop_image],
    )
    accept_conditions = serializers.BooleanField()

    def __init__(self, *args, shop=None, **kwargs):
        # The owner's existing shop, if any. A cover photo it already has
        # need not be sent again.
        self.shop = shop
        super().__init__(*args, **kwargs)

    def validate_accept_conditions(self, value):
        if value is not True:
            raise serializers.ValidationError(
                "Tick the box to accept LastPlate's conditions for restaurants."
            )

        return value

    def validate(self, attrs):
        validate_coordinate_pair(attrs)

        if not attrs.get("photo") and not (self.shop and self.shop.photo):
            raise serializers.ValidationError(
                {"photo": ["Add a cover photo of your restaurant."]}
            )

        return attrs


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


# LP-38: LastBags
KIND_IS_FIXED = (
    "An offer can't change between LastBag and specific item. "
    "Create a new offer instead."
)


def lastbag_title(shop):
    category = getattr(shop, "category", "")

    if category and category != Shop.Category.OTHER:
        return f"{category} surprise bag"

    return "Surprise bag"


# LP-10 and LP-11: Food listings (shop operator view)
class ListingSerializer(ListingDisplayMixin, serializers.ModelSerializer):

    class Meta:
        model = Listing

        fields = [
            "id",
            "kind",
            "title",
            "description",
            "category",
            "typical_contents",
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
        # LP-38: A draft stays the kind it was created as.
        if (
            self.instance is not None
            and "kind" in attrs
            and attrs["kind"] != self.instance.kind
        ):
            raise serializers.ValidationError(
                {"kind": [KIND_IS_FIXED]}
            )

        data = {}

        fields = [
            "kind",
            "title",
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
                kind=data["kind"],
            )
        except ValidationError as exc:
            raise serializers.ValidationError(
                exc.message_dict
            )

        # LP-38: The LastBag form has no title, so the bag is named after
        # the restaurant's category, for example "Bakery surprise bag".
        if data["kind"] == LASTBAG_KIND and not (data["title"] or "").strip():
            attrs["title"] = lastbag_title(self.context.get("shop"))

        return attrs


# LP-13: Public marketplace. Only what customers need; never the operator's
# user ID or sign-in email (the Shop model does not store the email at all).
class PublicShopSerializer(serializers.ModelSerializer):

    class Meta:
        model = Shop
        # LP-39: id links an offer to its restaurant page.
        fields = ["id", "name", "area", "public_contact"]
        read_only_fields = fields


# LP-39: A restaurant's own page, with the category and cover photo the
# owner gave in their LP-37 request (category is "Other" unless chosen).
class PublicShopDetailSerializer(PublicShopSerializer):

    address = serializers.CharField(read_only=True)
    category = serializers.SerializerMethodField()
    photo_display = serializers.SerializerMethodField()
    # LP-66: the logo from the LP-37 request, shown as a round badge.
    logo_display = serializers.SerializerMethodField()

    def get_category(self, obj):
        return getattr(obj, "category", "") or ""

    def get_photo_display(self, obj):
        photo = getattr(obj, "photo", None)
        return photo.url if photo else None

    def get_logo_display(self, obj):
        return image_url(obj.logo)

    class Meta(PublicShopSerializer.Meta):
        fields = [*PublicShopSerializer.Meta.fields, "address", "category", "photo_display", "logo_display"]
        read_only_fields = fields


# LP-37: The restaurant shown with each public offer, with its category,
# cover photo and logo.
class PublicShopCardSerializer(PublicShopSerializer):

    photo_display = serializers.SerializerMethodField()
    logo_display = serializers.SerializerMethodField()

    def get_photo_display(self, obj):
        return image_url(obj.photo)

    def get_logo_display(self, obj):
        return image_url(obj.logo)

    class Meta(PublicShopSerializer.Meta):
        fields = [
            *PublicShopSerializer.Meta.fields,
            "category",
            "photo_display",
            "logo_display",
        ]
        read_only_fields = fields


class PublicListingSerializer(ListingDisplayMixin, serializers.ModelSerializer):
    """
    A browsable offer. The view passes the already-loaded shops in
    context["shops"] ({shop_id: Shop}), so no query runs per listing.
    """

    shop = serializers.SerializerMethodField()

    # LP-38: Customers never see a LastBag's exact contents, only what it
    # usually holds (typical_contents).
    def get_ingredients_display(self, obj):
        if obj.kind == LASTBAG_KIND:
            return "Changes daily"
        return super().get_ingredients_display(obj)

    def get_shop(self, obj):
        shop = self.context["shops"][obj.shop_id]
        return PublicShopCardSerializer(shop).data

    class Meta:
        model = Listing
        fields = [
            "id",
            "kind",
            "title",
            "description",
            "category",
            "typical_contents",
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


LASTBAG = "LASTBAG"
ITEM = "ITEM"


class RestaurantOfferSerializer(PublicListingSerializer):
    """LP-39: an offer on its restaurant's page. LP-38 adds Listing.kind and the
    LastBag's typical contents; offers without a kind are specific items."""

    kind = serializers.SerializerMethodField()
    typical_contents = serializers.SerializerMethodField()

    def get_kind(self, obj):
        return getattr(obj, "kind", "") or ITEM

    def get_typical_contents(self, obj):
        return getattr(obj, "typical_contents", "") or ""

    class Meta(PublicListingSerializer.Meta):
        fields = [*PublicListingSerializer.Meta.fields, "kind", "typical_contents"]
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
