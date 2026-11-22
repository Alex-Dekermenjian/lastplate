"""LP-50: the refresh token issued when "Keep me logged in" is ticked."""

from datetime import timedelta

from rest_framework_simplejwt.tokens import RefreshToken

KEEP_LOGGED_IN_LIFETIME = timedelta(days=30)


class KeepLoggedInRefreshToken(RefreshToken):
    """A normal refresh token that lasts 30 days instead of 1.

    Only the lifetime differs. Refreshing, rotation settings and sign-out
    (blacklisting) work exactly as for the normal token, and the access tokens
    it gives still last 15 minutes.
    """

    lifetime = KEEP_LOGGED_IN_LIFETIME
