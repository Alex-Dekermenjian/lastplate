"""
Server-side storage for the Accounts service's access and refresh tokens.

Tokens are kept in the database-backed session. The browser only receives the
HttpOnly session ID cookie, never a token.
"""

import base64
import json
import time

ACCESS_TOKEN_KEY = "accounts_access_token"
REFRESH_TOKEN_KEY = "accounts_refresh_token"


def store_tokens(request, access, refresh):
    # A new session ID on sign-in prevents session fixation.
    request.session.cycle_key()
    request.session[ACCESS_TOKEN_KEY] = access
    request.session[REFRESH_TOKEN_KEY] = refresh


def set_access_token(request, access):
    request.session[ACCESS_TOKEN_KEY] = access


def get_access_token(request):
    return request.session.get(ACCESS_TOKEN_KEY)


def get_refresh_token(request):
    return request.session.get(REFRESH_TOKEN_KEY)


def clear_tokens(request):
    # Deletes the server-side session row and issues a fresh, empty session.
    request.session.flush()


def refresh_token_expired(request, now=None):
    """LP-18: True when the stored refresh token's own expiry time has passed.

    Only the expiry time is read; the signature is not checked here because
    this never grants anything. It only lets the web app end a session that has
    outlived its refresh token without waiting for a service call to fail.
    Accounts still decides whether any token is valid. A token that cannot be
    read counts as not expired, so Accounts gets the final say.
    """
    token = get_refresh_token(request)
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        expires_at = float(json.loads(base64.urlsafe_b64decode(payload))["exp"])
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        return False
    return expires_at <= (time.time() if now is None else now)