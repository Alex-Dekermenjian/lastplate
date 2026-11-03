from rest_framework.throttling import SimpleRateThrottle


class SignInEmailThrottle(SimpleRateThrottle):
    """Limits sign-in attempts per account email, whichever client sends them.

    Complements the per-client ScopedRateThrottle: once browsers reach this
    service through the web frontend, every request shares the frontend's
    address, so only a per-email limit still protects individual accounts.
    """

    scope = "auth_sign_in_email"

    def get_cache_key(self, request, view):
        email = str(request.data.get("email", "")).strip().lower()
        if not email:
            return None  # nothing to key on; the per-client limit still applies
        return self.cache_format % {"scope": self.scope, "ident": email}
