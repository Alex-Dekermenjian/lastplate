"""
Server-side storage for the Accounts service's access and refresh tokens.

Tokens are kept in the database-backed session. The browser only receives the
HttpOnly session ID cookie, never a token.
"""

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
