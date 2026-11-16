"""LP-20: who may call Marketplace's internal endpoints.

docs/microservices-plan.md §4, "Internal endpoint authentication":
- each call sends X-Service-Name (e.g. "reservations") and X-Service-Key;
- Marketplace keeps one key per allowed caller, only in environment
  variables (settings.INTERNAL_SERVICE_KEYS), never in Git;
- keys are compared in constant time (hmac.compare_digest) and are never
  logged or returned in an error;
- each endpoint lists which callers it allows;
- user JWTs are ignored here, and service keys are ignored everywhere else.

Missing headers → 401. Unknown caller, wrong key, or a caller this endpoint
does not allow → 403.
"""

import functools
import hmac
import logging

from django.conf import settings
from rest_framework.response import Response

logger = logging.getLogger("marketplace.internal")

NAME_HEADER = "X-Service-Name"
KEY_HEADER = "X-Service-Key"

# Compared against when the caller is unknown, so the time taken does not
# tell an attacker which caller names exist.
_DUMMY_KEY = b"x" * 43


def _refuse(request, http_status, code, detail, caller):
    # The caller name is untrusted input: only a short repr is logged, and
    # the key never is.
    logger.warning("Refused internal call to %s: %s (caller %r)", request.path, code, caller[:40])
    response = Response({"code": code, "detail": detail}, status=http_status)
    if http_status == 401:
        response["WWW-Authenticate"] = KEY_HEADER
    return response


def _key_matches(presented, configured_keys):
    presented = presented.encode("utf-8")
    matched = False
    for key in configured_keys or [_DUMMY_KEY.decode()]:
        # No early exit: every configured key is compared.
        matched |= hmac.compare_digest(presented, key.encode("utf-8"))
    return matched and bool(configured_keys)


def check_service_caller(request, allowed_callers):
    """None if the caller may use this endpoint, else the refusal Response."""
    caller = request.headers.get(NAME_HEADER, "").strip()
    presented = request.headers.get(KEY_HEADER, "")

    if not caller or not presented:
        return _refuse(request, 401, "service_auth_required",
                       "This endpoint needs X-Service-Name and X-Service-Key.", caller)

    configured = settings.INTERNAL_SERVICE_KEYS.get(caller, [])
    if not _key_matches(presented, configured):
        if caller in settings.INTERNAL_SERVICE_KEYS and not configured:
            logger.error("No service key is configured for caller %r; set it in the environment.", caller)
        return _refuse(request, 403, "invalid_service_key", "Unknown service or wrong service key.", caller)

    if caller not in allowed_callers:
        return _refuse(request, 403, "caller_not_allowed", "This service may not call this endpoint.", caller)

    return None


def internal_endpoint(allowed_callers):
    """Decorator for internal views: check the service key before the view runs."""

    def decorator(view):
        @functools.wraps(view)
        def wrapped(request, *args, **kwargs):
            refusal = check_service_caller(request, allowed_callers)
            if refusal is not None:
                return refusal
            return view(request, *args, **kwargs)

        return wrapped

    return decorator
