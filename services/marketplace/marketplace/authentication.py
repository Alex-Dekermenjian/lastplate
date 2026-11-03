from dataclasses import dataclass

import jwt
from django.conf import settings
from rest_framework import authentication, exceptions
from rest_framework.permissions import BasePermission

INVALID_TOKEN = "Invalid or expired access token."


@dataclass(frozen=True)
class TokenUser:
    """The caller named by a verified Accounts access token.

    Marketplace stores no users: identity and role come only from the token.
    """

    id: int
    role: str
    is_staff: bool
    is_authenticated = True


class AccessTokenAuthentication(authentication.BaseAuthentication):
    """Verifies Accounts-issued access tokens with the RS256 public key only.

    Uses PyJWT directly because SimpleJWT's stateless authentication imports
    Django's auth models, which would add unused user tables to this service.
    """

    keyword = "Bearer"

    def authenticate(self, request):
        header = authentication.get_authorization_header(request).split()
        if not header or header[0].lower() != self.keyword.lower().encode():
            return None
        if len(header) != 2:
            raise exceptions.AuthenticationFailed(INVALID_TOKEN)

        try:
            # Pinning RS256 rejects "none" and HMAC tokens signed with the
            # public key; exp is required and checked by PyJWT.
            claims = jwt.decode(
                header[1],
                settings.JWT_PUBLIC_KEY,
                algorithms=["RS256"],
                options={"require": ["exp", "token_type", "user_id"]},
            )
            user_id = int(claims["user_id"])
        except (jwt.PyJWTError, TypeError, ValueError):
            raise exceptions.AuthenticationFailed(INVALID_TOKEN)
        if claims["token_type"] != "access" or user_id <= 0:
            raise exceptions.AuthenticationFailed(INVALID_TOKEN)

        user = TokenUser(id=user_id, role=str(claims.get("role", "")), is_staff=claims.get("is_staff") is True)
        return user, claims

    def authenticate_header(self, request):
        return self.keyword


class IsShopOperator(BasePermission):
    message = "Only shop operators can do this."

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.role == "SHOP_OPERATOR")
