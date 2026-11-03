import re

from rest_framework import serializers

from .models import Shop

# Public contact is a phone number customers can call. Accepted: digits with
# spaces, hyphens, dots, slashes or brackets and an optional leading "+";
# 7-15 digits in total (E.164 allows at most 15). Not an email address: the
# operator's sign-in email must never become public by accident.
PHONE_CHARACTERS = re.compile(r"\+?[0-9 ()./-]+")


def validate_public_contact(value):
    if not PHONE_CHARACTERS.fullmatch(value):
        raise serializers.ValidationError(
            "Enter a phone number using digits, spaces, +, -, brackets or dots, for example +961 1 234 567."
        )
    digits = re.sub(r"\D", "", value)
    if not 7 <= len(digits) <= 15:
        raise serializers.ValidationError("Enter a full phone number with 7 to 15 digits, for example +961 1 234 567.")
    if len(set(digits)) == 1:
        raise serializers.ValidationError("Enter a real phone number customers can call.")
    return value


class RejectUnknownFieldsMixin:
    """Reject, rather than silently drop, fields the operator may not set
    (ownership, approval status, rejection reason, timestamps)."""

    def to_internal_value(self, data):
        unexpected = sorted(set(data) - set(self.fields))
        if unexpected:
            raise serializers.ValidationError(
                {name: ["This field is not allowed."] for name in unexpected}
            )
        return super().to_internal_value(data)


class ShopCreateSerializer(RejectUnknownFieldsMixin, serializers.Serializer):
    """Only the shop name is accepted. Ownership comes from the token and
    approval status is always Pending, so neither may be submitted."""

    name = serializers.CharField(max_length=150)


class ShopProfileSerializer(RejectUnknownFieldsMixin, serializers.Serializer):
    """The operator-editable profile. Any subset may be sent; a sent field
    may not be blank, so a completed detail cannot be erased by mistake.
    Minimum lengths reject values no customer could use (for example "x")."""

    name = serializers.CharField(min_length=2, max_length=150, required=False)
    address = serializers.CharField(min_length=5, max_length=255, required=False)
    area = serializers.CharField(min_length=2, max_length=100, required=False)
    public_contact = serializers.CharField(max_length=32, required=False, validators=[validate_public_contact])

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError("Send at least one profile field to change.")
        return attrs


class ShopSerializer(serializers.ModelSerializer):
    profile_complete = serializers.BooleanField(read_only=True)

    class Meta:
        model = Shop
        fields = [
            "id", "operator_user_id", "name", "address", "area", "public_contact",
            "approval_status", "rejection_reason", "profile_complete", "created_at", "updated_at",
        ]
        read_only_fields = fields
