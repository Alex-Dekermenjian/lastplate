"""LP-28: authentication for /internal/ endpoints, called by other services.

The caller sends X-Service-Name and X-Service-Key. Each allowed caller has
its own key (settings.INTERNAL_SERVICE_KEYS); keys are compared in constant
time and never logged. User tokens are ignored here
(docs/microservices-plan.md section 4, "Internal endpoint authentication").
"""

import hmac
from dataclasses import dataclass

from django.conf import settings
from rest_framework import authentication, exceptions
from rest_framework.permissions import BasePermission


@dataclass(frozen=True)
class ServiceCaller:
    name: str
    is_authenticated = True


class ServiceKeyAuthentication(authentication.BaseAuthentication):
    def authenticate(self, request):
        name = request.headers.get("X-Service-Name", "")
        key = request.headers.get("X-Service-Key", "")
        if not name or not key:
            return None
        expected = settings.INTERNAL_SERVICE_KEYS.get(name, "")
        # An unconfigured (empty) key never matches.
        if not expected or not hmac.compare_digest(key.encode(), expected.encode()):
            raise exceptions.AuthenticationFailed("Unknown service or wrong key.")
        return ServiceCaller(name), None

    def authenticate_header(self, request):
        return "Service-Key"


def only_service(name):
    """Permission: this endpoint accepts only the named calling service."""

    class OnlyService(BasePermission):
        message = "This service may not call this endpoint."

        def has_permission(self, request, view):
            return isinstance(request.user, ServiceCaller) and request.user.name == name

    return OnlyService
