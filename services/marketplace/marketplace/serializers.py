from rest_framework import serializers

from .models import Shop


class ShopCreateSerializer(serializers.Serializer):
    """Only the shop name is accepted. Ownership comes from the token and
    approval status is always Pending, so neither may be submitted."""

    name = serializers.CharField(max_length=150)

    def to_internal_value(self, data):
        unexpected = sorted(set(data) - set(self.fields))
        if unexpected:
            raise serializers.ValidationError(
                {name: ["This field is not allowed."] for name in unexpected}
            )
        return super().to_internal_value(data)


class ShopSerializer(serializers.ModelSerializer):
    class Meta:
        model = Shop
        fields = ["id", "operator_user_id", "name", "approval_status", "created_at", "updated_at"]
        read_only_fields = fields
