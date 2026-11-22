from django.contrib.auth import get_user_model, password_validation
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from rest_framework import serializers
from django.contrib.auth.models import update_last_login
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer, TokenObtainSerializer
from rest_framework_simplejwt.settings import api_settings as jwt_settings

from .tokens import KeepLoggedInRefreshToken

User = get_user_model()

DUPLICATE_EMAIL = "An account with this email already exists."
INVALID_CREDENTIALS = "Email or password is incorrect."


class ShopOperatorRegistrationSerializer(serializers.Serializer):
    """Public shop-operator sign-up. Only these three fields are accepted;
    the role is always set by the server."""

    account_role = User.Role.SHOP_OPERATOR

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
                return User.objects.create_user(**validated_data, role=self.account_role)
        except IntegrityError:
            # A concurrent sign-up with the same email won the race.
            raise serializers.ValidationError({"email": [DUPLICATE_EMAIL]})


class CustomerRegistrationSerializer(ShopOperatorRegistrationSerializer):
    """Public customer sign-up (LP-56). Same three fields and checks; a role
    sent by the client is still rejected, never used."""

    account_role = User.Role.CUSTOMER


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        # is_staff lets the web send team members to the admin site after login.
        fields = ["id", "email", "display_name", "role", "is_staff"]
        read_only_fields = fields


class MeSerializer(UserSerializer):
    """GET /api/users/me/. LP-50: must_change_password sends the person to
    "Choose a new password" before anything else."""

    class Meta(UserSerializer.Meta):
        fields = UserSerializer.Meta.fields + ["must_change_password"]
        read_only_fields = fields


class ProfileUpdateSerializer(serializers.Serializer):
    """LP-18: a signed-in user may change their own display name and nothing
    else. Email, role and staff flags are rejected, never silently ignored."""

    display_name = serializers.CharField(max_length=150)

    def to_internal_value(self, data):
        unexpected = sorted(set(data) - set(self.fields))
        if unexpected:
            raise serializers.ValidationError(
                {name: ["This field is not allowed."] for name in unexpected}
            )
        return super().to_internal_value(data)

    def update(self, instance, validated_data):
        instance.display_name = validated_data["display_name"]
        instance.save(update_fields=["display_name"])
        return instance


class SignInSerializer(TokenObtainPairSerializer):
    """Email and password sign-in with one message for every credential failure."""

    default_error_messages = {"no_active_account": INVALID_CREDENTIALS}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # LP-14: Registration keeps spaces in passwords, so sign-in must too,
        # or such an account could never sign in.
        self.fields["password"].trim_whitespace = False
        # LP-50: "Keep me logged in" makes the refresh token last 30 days.
        # Without it the refresh token keeps the usual lifetime (1 day).
        self.fields["remember_me"] = serializers.BooleanField(required=False, default=False)

    def validate(self, attrs):
        keep_logged_in = attrs.pop("remember_me", False)
        # Checks the email and password and sets self.user (no token yet).
        data = TokenObtainSerializer.validate(self, attrs)
        refresh = self.get_token(self.user, KeepLoggedInRefreshToken if keep_logged_in else None)
        data["refresh"] = str(refresh)
        data["access"] = str(refresh.access_token)
        if jwt_settings.UPDATE_LAST_LOGIN:
            update_last_login(None, self.user)
        return data

    @classmethod
    def get_token(cls, user, token_class=None):
        token = (token_class or cls.token_class).for_user(user)
        token["role"] = user.role
        token["display_name"] = user.display_name
        token["is_staff"] = user.is_staff
        return token


class ChangePasswordSerializer(serializers.Serializer):
    """LP-50: the signed-in user replaces their password. The current one
    (for example the temporary password an admin gave) must be right, and the
    new one passes Django's normal password validation."""

    current_password = serializers.CharField(write_only=True, trim_whitespace=False, max_length=128)
    new_password = serializers.CharField(write_only=True, trim_whitespace=False, max_length=128)

    def validate_current_password(self, value):
        if not self.instance.check_password(value):
            raise serializers.ValidationError("Your current password is incorrect.")
        return value

    def validate(self, attrs):
        if attrs["new_password"] == attrs["current_password"]:
            raise serializers.ValidationError({"new_password": ["Choose a password different from the current one."]})
        try:
            password_validation.validate_password(attrs["new_password"], user=self.instance)
        except DjangoValidationError as error:
            raise serializers.ValidationError({"new_password": list(error.messages)})
        return attrs

    def update(self, instance, validated_data):
        instance.set_password(validated_data["new_password"])
        instance.must_change_password = False
        instance.save(update_fields=["password", "must_change_password"])
        return instance


class AdminUserSerializer(serializers.ModelSerializer):
    """LP-50: what /console shows when an admin looks up an account."""

    can_get_temporary_password = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "email", "display_name", "role", "must_change_password", "can_get_temporary_password"]
        read_only_fields = fields

    def get_can_get_temporary_password(self, user):
        return can_get_temporary_password(user)


def can_get_temporary_password(user):
    """Only customer and restaurant accounts. Admin and staff accounts are
    recovered with create_admin or the Django admin, so one admin can never
    take over another's account from /console."""
    return (
        user.is_active
        and user.role in (User.Role.CUSTOMER, User.Role.SHOP_OPERATOR)
        and not user.is_staff
        and not user.is_superuser
    )
