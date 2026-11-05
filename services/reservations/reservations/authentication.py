"""LP-28: verify Accounts-issued access tokens with the RS256 public key only.

The same checks as Marketplace's authentication: RS256 pinned, exp, token
type and user ID required. Reservations stores no users.
"""

from dataclasses import dataclass

import jwt
from django.conf import settings
from rest_framework import authentication, exceptions
from rest_framework.permissions import BasePermission

INVALID_TOKEN = "Invalid or expired access token."


@dataclass(frozen=True)
class TokenUser:
    id: int
    role: str
    is_authenticated = True


class AccessTokenAuthentication(authentication.BaseAuthentication):
    keyword = "Bearer"

    def authenticate(self, request):
        header = authentication.get_authorization_header(request).split()
        if not header or header[0].lower() != self.keyword.lower().encode():
            return None
        if len(header) != 2 or not settings.JWT_PUBLIC_KEY:
            raise exceptions.AuthenticationFailed(INVALID_TOKEN)
        try:
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
        return TokenUser(id=user_id, role=str(claims.get("role", ""))), claims

    def authenticate_header(self, request):
        return self.keyword


class IsShopOperator(BasePermission):
    message = "Only restaurant accounts can do this."

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.role == "SHOP_OPERATOR")
