"""Calls to the Accounts, Marketplace and Reservations APIs.

The frontend never touches another service's database. Each call returns
(status, data). A timeout, connection failure or 5xx response raises
ServiceUnavailable: the caller cannot tell whether the request took effect.
Passwords and tokens are never logged or put in exception messages.
"""

import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
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
def _call(service, method, path, payload=None, token=None, timeout=None):
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
            timeout=timeout or settings.SERVICE_API_TIMEOUT_SECONDS,
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


REGISTER_PATHS = {
    "restaurant": "/api/auth/register/",
    "customer": "/api/auth/register/customer/",
}


@sensitive_variables("password")
def register(display_name, email, password, account_type="restaurant"):
    """Create an account. Only the two public account types exist; the role is
    chosen by which Accounts endpoint is called, never sent as a field."""
    return _call(
        "accounts",
        "POST",
        REGISTER_PATHS[account_type],
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
def update_shop(access, profile, images=None):
    """LP-8 profile edit. LP-59: a new logo or cover photo goes as multipart."""
    if images:
        return _call_multipart(
            "marketplace",
            "PATCH",
            "/api/shops/me/",
            fields=profile,
            files=images,
            token=access,
        )
    return _call(
        "marketplace",
        "PATCH",
        "/api/shops/me/",
        profile,
        token=access,
    )


# LP-37: Send the restaurant's request to join LastPlate (with its images).
@sensitive_variables("access", "files")
def submit_application(access, fields, files):
    return _call_multipart(
        "marketplace",
        "POST",
        "/api/shops/me/application/",
        fields=fields,
        files=files,
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


# LP-45: a rejected restaurant asks for review again (Pending, never Approved).
@sensitive_variables("access")
def review_again(access, note):
    return _call("marketplace", "POST", "/api/shops/me/review-again", {"note": note}, token=access)


# LP-58: quick actions from the owner's offer list.
@sensitive_variables("access")
def mark_sold_out(access, listing_id):
    return _call("marketplace", "POST", f"/api/listings/{int(listing_id)}/sold-out", token=access)


@sensitive_variables("access")
def delete_draft(access, listing_id):
    return _call("marketplace", "POST", f"/api/listings/{int(listing_id)}/delete", token=access)


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


# LP-57: Admin API for /console. Marketplace accepts only Admin tokens here.
@sensitive_variables("access")
def admin_summary(access):
    return _call("marketplace", "GET", "/api/admin/summary", token=access)


@sensitive_variables("access")
def admin_shops(access, approval_status):
    return _call("marketplace", "GET", f"/api/admin/shops?{urlencode({'status': approval_status})}", token=access)


@sensitive_variables("access")
def admin_shop(access, shop_id):
    return _call("marketplace", "GET", f"/api/admin/shops/{int(shop_id)}", token=access)


@sensitive_variables("access")
def admin_review_shop(access, shop_id, decision):
    return _call("marketplace", "POST", f"/api/admin/shops/{int(shop_id)}/review", decision, token=access)


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


# LP-39: A restaurant's public page. No token: anyone may open it.
def restaurant_page(shop_id):
    return _call(
        "marketplace",
        "GET",
        f"/api/shops/{int(shop_id)}",
    )


# LP-68: the owner's branches. Marketplace finds the restaurant from the token.
@sensitive_variables("access")
def my_branches(access):
    return _call("marketplace", "GET", "/api/shops/me/branches/", token=access)


@sensitive_variables("access")
def add_branch(access, branch):
    return _call("marketplace", "POST", "/api/shops/me/branches/", branch, token=access)


@sensitive_variables("access")
def update_branch(access, branch_id, changes):
    return _call("marketplace", "PATCH", f"/api/shops/me/branches/{int(branch_id)}/", changes, token=access)


@sensitive_variables("access")
def remove_branch(access, branch_id):
    return _call("marketplace", "DELETE", f"/api/shops/me/branches/{int(branch_id)}/", token=access)


# LP-19: checkout. The answer can take several Marketplace calls (one per
# item), so it gets a longer timeout than other calls.
@sensitive_variables("access")
def create_reservation(access, request_key, lines):
    return _call(
        "reservations", "POST", "/api/reservations",
        {"request_key": request_key, "lines": lines},
        token=access, timeout=settings.CHECKOUT_TIMEOUT_SECONDS,
    )


# LP-78: the customer's own orders (Reservations answers only for this token's customer).
@sensitive_variables("access")
def my_orders(access):
    return _call("reservations", "GET", "/api/reservations", token=access)


@sensitive_variables("access")
def get_order(access, reservation_ref):
    return _call("reservations", "GET", f"/api/reservations/{reservation_ref}", token=access)
