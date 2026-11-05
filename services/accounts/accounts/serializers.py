from django.contrib.auth import get_user_model, password_validation
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

User = get_user_model()

DUPLICATE_EMAIL = "An account with this email already exists."
INVALID_CREDENTIALS = "Email or password is incorrect."


class RegistrationSerializer(serializers.Serializer):
    """Public sign-up. Only these three fields are accepted; the role comes
    from the subclass, never from the request."""

    role = None

    display_name = serializers.CharField(max_length=150)
    email = serializers.EmailField(max_length=254)
    password = serializers.CharField(write_only=True, trim_whitespace=False, max_length=128)

    def to_internal_value(self, data):
        # Reject, rather than silently drop, fields such as role, is_staff or
        # approval_status so forged requests fail visibly.
        unexpected = sorted(set(data) - set(self.fields))
        if unexpected:
            raise serializers.ValidationError(
                {name: ["This field is not allowed."] for name in unexpected}
            )
        return super().to_internal_value(data)

    def validate_email(self, value):
        email = User.objects.normalize_email(value.strip()).lower()
        if User.objects.filter(email__iexact=email).exists():
            raise serializers.ValidationError(DUPLICATE_EMAIL)
        return email

    def validate(self, attrs):
        candidate = User(email=attrs["email"], display_name=attrs["display_name"])
        try:
            password_validation.validate_password(attrs["password"], user=candidate)
        except DjangoValidationError as error:
            raise serializers.ValidationError({"password": list(error.messages)})
        return attrs

    def create(self, validated_data):
        try:
            with transaction.atomic():
                # create_user never sets is_staff or is_superuser.
                return User.objects.create_user(**validated_data, role=self.role)
        except IntegrityError:
            # A concurrent sign-up with the same email won the race.
            raise serializers.ValidationError({"email": [DUPLICATE_EMAIL]})


class ShopOperatorRegistrationSerializer(RegistrationSerializer):
    role = User.Role.SHOP_OPERATOR


class CustomerRegistrationSerializer(RegistrationSerializer):
    role = User.Role.CUSTOMER


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "email", "display_name", "role"]
        read_only_fields = fields


class SignInSerializer(TokenObtainPairSerializer):
    """Email and password sign-in with one message for every credential failure."""

    default_error_messages = {"no_active_account": INVALID_CREDENTIALS}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # LP-14: Registration keeps spaces in passwords, so sign-in must too,
        # or such an account could never sign in.
        self.fields["password"].trim_whitespace = False

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token["role"] = user.role
        token["display_name"] = user.display_name
        token["is_staff"] = user.is_staff
        return token
