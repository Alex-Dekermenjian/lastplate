"""Calls to the Accounts and Marketplace APIs.

The frontend never touches another service's database. Each call returns
(status, data). A timeout, connection failure or 5xx response raises
ServiceUnavailable: the caller cannot tell whether the request took effect.
Passwords and tokens are never logged or put in exception messages.
"""

import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

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

    request = Request(
        settings.SERVICE_URLS[service] + path,
        data=body,
        method=method,
        headers=headers,
    )

    try:
        with urlopen(
            request,
            timeout=settings.SERVICE_API_TIMEOUT_SECONDS,
        ) as response:
            return response.status, _read_json(response)
    except HTTPError as error:
        if error.code >= 500:
            raise ServiceUnavailable(service) from None
        return error.code, _read_json(error)
    except (URLError, TimeoutError, OSError):
        raise ServiceUnavailable(service) from None


def _encode_multipart(fields=None, files=None):
    fields = fields or {}
    files = files or {}

    boundary = f"----LastPlateBoundary{uuid4().hex}"
    chunks = []

    def add_text(value):
        chunks.append(value.encode("utf-8"))

    for name, value in fields.items():
        add_text(f"--{boundary}\r\n")
        add_text(
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
        )
        add_text(str(value))
        add_text("\r\n")

    for field_name, uploaded_file in files.items():
        if hasattr(uploaded_file, "seek"):
            uploaded_file.seek(0)

        filename = os.path.basename(
            getattr(uploaded_file, "name", "upload.bin")
        )
        content_type = (
            getattr(uploaded_file, "content_type", None)
            or "application/octet-stream"
        )
        file_bytes = uploaded_file.read()

        add_text(f"--{boundary}\r\n")
        add_text(
            f'Content-Disposition: form-data; name="{field_name}"; '
            f'filename="{filename}"\r\n'
        )
        add_text(f"Content-Type: {content_type}\r\n\r\n")
        chunks.append(file_bytes)
        add_text("\r\n")

    add_text(f"--{boundary}--\r\n")

    return boundary, b"".join(chunks)


@sensitive_variables("fields", "files", "token", "body", "headers")
def _call_multipart(
    service,
    method,
    path,
    fields=None,
    files=None,
    token=None,
):
    boundary, body = _encode_multipart(
        fields=fields,
        files=files,
    )

    headers = {
        "Accept": "application/json",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
    }

    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = Request(
        settings.SERVICE_URLS[service] + path,
        data=body,
        method=method,
        headers=headers,
    )

    try:
        with urlopen(
            request,
            timeout=settings.SERVICE_API_TIMEOUT_SECONDS,
        ) as response:
            return response.status, _read_json(response)
    except HTTPError as error:
        if error.code >= 500:
            raise ServiceUnavailable(service) from None
        return error.code, _read_json(error)
    except (URLError, TimeoutError, OSError):
        raise ServiceUnavailable(service) from None


@sensitive_variables("password")
def register(display_name, email, password):
    return _call(
        "accounts",
        "POST",
        "/api/auth/register/",
        {
            "display_name": display_name,
            "email": email,
            "password": password,
        },
    )


@sensitive_variables("password")
def register_customer(display_name, email, password):
    return _call(
        "accounts",
        "POST",
        "/api/auth/register/customer/",
        {
            "display_name": display_name,
            "email": email,
            "password": password,
        },
    )


@sensitive_variables("password")
def sign_in(email, password):
    return _call(
        "accounts",
        "POST",
        "/api/auth/token/",
        {
            "email": email,
            "password": password,
        },
    )


@sensitive_variables("access")
def account(access):
    return _call(
        "accounts",
        "GET",
        "/api/users/me/",
        token=access,
    )


@sensitive_variables("refresh_token")
def refresh(refresh_token):
    return _call(
        "accounts",
        "POST",
        "/api/auth/token/refresh/",
        {"refresh": refresh_token},
    )


@sensitive_variables("refresh_token")
def sign_out(refresh_token):
    return _call(
        "accounts",
        "POST",
        "/api/auth/logout/",
        {"refresh": refresh_token},
    )


@sensitive_variables("access")
def create_shop(access, name):
    return _call(
        "marketplace",
        "POST",
        "/api/shops/",
        {"name": name},
        token=access,
    )


@sensitive_variables("access")
def my_shop(access):
    return _call(
        "marketplace",
        "GET",
        "/api/shops/me/",
        token=access,
    )


@sensitive_variables("access")
def update_shop(access, profile):
    return _call(
        "marketplace",
        "PATCH",
        "/api/shops/me/",
        profile,
        token=access,
    )


# LP-10 and LP-11: Retrieve the authenticated shop's food listings.
@sensitive_variables("access")
def my_listings(access):
    return _call(
        "marketplace",
        "GET",
        "/api/listings/mine",
        token=access,
    )


# LP-10: Create a new food listing as a draft.
@sensitive_variables("access")
def create_listing(access, listing_data):
    return _call(
        "marketplace",
        "POST",
        "/api/listings",
        listing_data,
        token=access,
    )


# LP-11: Upload or replace a draft listing photo.
@sensitive_variables("access", "uploaded_file")
def upload_listing_photo(access, listing_id, uploaded_file):
    return _call_multipart(
        "marketplace",
        "POST",
        f"/api/listings/{listing_id}/photo",
        files={"photo": uploaded_file},
        token=access,
    )

# LP-10: Edit an existing draft listing.
@sensitive_variables("access")
def edit_listing(access, listing_id, listing_data):
    return _call(
        "marketplace",
        "PATCH",
        f"/api/listings/{listing_id}",
        listing_data,
        token=access,
    )

# LP-9 and LP-10: Publish an approved shop's draft listing.
@sensitive_variables("access")
def publish_listing(access, listing_id):
    return _call(
        "marketplace",
        "POST",
        f"/api/listings/{listing_id}/publish",
        {},
        token=access,
    )


# LP-12: Change a published listing's stock with a reason.
@sensitive_variables("access")
def adjust_stock(access, listing_id, quantity_change, reason):
    return _call(
        "marketplace",
        "POST",
        f"/api/listings/{listing_id}/stock-adjustments",
        {
            "quantity_change": quantity_change,
            "reason": reason,
        },
        token=access,
    )


# LP-12: A listing's stock adjustment history, newest first.
@sensitive_variables("access")
def stock_history(access, listing_id):
    return _call(
        "marketplace",
        "GET",
        f"/api/listings/{listing_id}/stock-adjustments",
        token=access,
    )


# LP-13: Public marketplace. No token is sent: these pages need no sign-in.
def browse_listings():
    return _call(
        "marketplace",
        "GET",
        "/api/listings",
    )


def listing_offer(listing_id):
    return _call(
        "marketplace",
        "GET",
        f"/api/listings/{int(listing_id)}",
    )
