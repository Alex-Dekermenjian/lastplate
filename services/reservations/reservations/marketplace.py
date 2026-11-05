"""LP-28: calls to Marketplace's /internal/ endpoints with this service's key.

Each call returns (status, body). A timeout, connection failure or 5xx raises
MarketplaceUnavailable: the outcome is unknown, so nothing may be assumed
(docs/microservices-plan.md section 6). The key is never logged.
"""

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.views.decorators.debug import sensitive_variables


class MarketplaceUnavailable(Exception):
    pass


@sensitive_variables("headers")
def _post(path, payload):
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-Service-Name": "reservations",
        "X-Service-Key": settings.MARKETPLACE_SERVICE_KEY,
    }
    request = Request(
        settings.MARKETPLACE_SERVICE_URL + path,
        data=json.dumps(payload).encode(),
        method="POST",
        headers=headers,
    )
    try:
        with urlopen(request, timeout=settings.SERVICE_API_TIMEOUT_SECONDS) as response:
            return response.status, json.load(response)
    except HTTPError as error:
        if error.code >= 500:
            raise MarketplaceUnavailable from None
        try:
            return error.code, json.load(error)
        except ValueError:
            return error.code, {}
    except (URLError, TimeoutError, OSError, ValueError):
        raise MarketplaceUnavailable from None


def allocate(lines):
    """Hold stock for all of an order's lines together, or for none.
    lines: [{"reservation_ref", "listing_id", "quantity"}, ...]"""
    return _post("/internal/allocations", {"lines": lines})


def release(allocation_ref, listing_id):
    return _post(f"/internal/allocations/{allocation_ref}/release", {"listing_id": listing_id})
