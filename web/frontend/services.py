"""Calls to the Accounts and Marketplace APIs.

The frontend never touches another service's database. Each call returns
(status, data). A timeout, connection failure or 5xx response raises
ServiceUnavailable: the caller cannot tell whether the request took effect.
Passwords and tokens are never logged or put in exception messages.
"""

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.views.decorators.debug import sensitive_variables


class ServiceUnavailable(Exception):
    """No usable answer from a service; the outcome of the request is unknown."""


def _read_json(response):
    try:
        return json.load(response)
    except ValueError:
        return {}


@sensitive_variables("payload", "token", "body", "headers")
def _call(service, method, path, payload=None, token=None):
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(settings.SERVICE_URLS[service] + path, data=body, method=method, headers=headers)
    try:
        with urlopen(request, timeout=settings.SERVICE_API_TIMEOUT_SECONDS) as response:
            return response.status, _read_json(response)
    except HTTPError as error:
        if error.code >= 500:
            raise ServiceUnavailable(service) from None
        return error.code, _read_json(error)
    except (URLError, TimeoutError, OSError):
        raise ServiceUnavailable(service) from None


@sensitive_variables("password")
def register(display_name, email, password):
    return _call("accounts", "POST", "/api/auth/register/",
                 {"display_name": display_name, "email": email, "password": password})


@sensitive_variables("password")
def sign_in(email, password):
    return _call("accounts", "POST", "/api/auth/token/", {"email": email, "password": password})


@sensitive_variables("access")
def account(access):
    return _call("accounts", "GET", "/api/users/me/", token=access)


@sensitive_variables("refresh_token")
def refresh(refresh_token):
    return _call("accounts", "POST", "/api/auth/token/refresh/", {"refresh": refresh_token})


@sensitive_variables("refresh_token")
def sign_out(refresh_token):
    return _call("accounts", "POST", "/api/auth/logout/", {"refresh": refresh_token})


@sensitive_variables("access")
def create_shop(access, name):
    return _call("marketplace", "POST", "/api/shops/", {"name": name}, token=access)


@sensitive_variables("access")
def my_shop(access):
    return _call("marketplace", "GET", "/api/shops/me/", token=access)
