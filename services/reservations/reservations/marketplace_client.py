"""LP-19: Reservations' calls to Marketplace.

- read_offer: the public offer API, to recheck an item before ordering.
- allocate / release: the internal stock API (LP-20), with this service's
  name and key in X-Service-Name / X-Service-Key. The key is never logged.

Answers are sorted the way docs/microservices-plan.md §6 says:
- HELD: the units are held;
- REJECTED: a 409 carrying the stored REJECTED allocation for this ref, the
  only definitive "no";
- UNKNOWN: timeout, connection error or 5xx. The outcome is not known, so
  nothing may be released or decided because of it;
- FAULT: any other answer (400, 401, 403, 404, a broken body): a set-up or
  contract fault for staff, never a stock refusal.
"""

import json
import logging
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings

logger = logging.getLogger("reservations.marketplace")

SERVICE_NAME = "reservations"

HELD = "HELD"
REJECTED = "REJECTED"
RELEASED = "RELEASED"
UNKNOWN = "UNKNOWN"
FAULT = "FAULT"


class MarketplaceUnavailable(Exception):
    """No usable answer from Marketplace; the outcome is unknown."""


@dataclass(frozen=True)
class Answer:
    """The sorted answer to an allocate or release call."""

    kind: str
    allocation: dict | None = None
    code: str = ""


def _request(method, path, payload=None, internal=False):
    """(status, body) from Marketplace. Raises MarketplaceUnavailable when the
    outcome is unknown: timeout, connection failure or a 5xx answer."""
    headers = {"Accept": "application/json"}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if internal:
        headers["X-Service-Name"] = SERVICE_NAME
        headers["X-Service-Key"] = settings.MARKETPLACE_SERVICE_KEY
    request = Request(settings.MARKETPLACE_SERVICE_URL + path, data=data, method=method, headers=headers)
    try:
        with urlopen(request, timeout=settings.MARKETPLACE_TIMEOUT_SECONDS) as response:
            return response.status, _json(response.read())
    except HTTPError as error:
        if error.code >= 500:
            raise MarketplaceUnavailable(f"Marketplace answered {error.code}") from None
        return error.code, _json(error.read())
    except (URLError, TimeoutError, OSError):
        # The message names no header, so the key can never end up in it.
        raise MarketplaceUnavailable("Marketplace could not be reached") from None


def _json(raw):
    try:
        return json.loads(raw or b"null")
    except ValueError:
        return None


def read_offer(listing_id):
    """(status, offer) from the public offer API. 404: not on sale any more
    (sold out, taken down, or pickup ended)."""
    status, body = _request("GET", f"/api/listings/{int(listing_id)}")
    return status, (body if isinstance(body, dict) else None)


def operator_shop(user_id):
    if not settings.MARKETPLACE_SERVICE_KEY:
        logger.error("Marketplace service key is not configured; operator ownership lookup is unavailable.")
        raise MarketplaceUnavailable("Marketplace service key is not configured")
    return _request("GET", f"/internal/shops/by-operator/{int(user_id)}", internal=True)


def _held(body, ref):
    return isinstance(body, dict) and body.get("reservation_ref") == str(ref)


# LP-25: what the timer job's release and consume calls can end in.
DONE = "DONE"
# The allocation states that mean the call has taken effect.
_FINISHED_BY = {"release": ("RELEASED", "REJECTED"), "consume": ("CONSUMED",)}


def stock_call(action, ref, listing_id):
    """LP-25: release or consume one item's allocation.

    DONE: Marketplace confirmed it (a repeat answers the same, it is
    idempotent). UNKNOWN: no answer, try again on the next run. FAULT: an
    answer that must never happen (409 not_held, 404, a bad key): staff look.
    """
    if action not in _FINISHED_BY:
        raise ValueError(f"Unknown stock action {action!r}")
    if not settings.MARKETPLACE_SERVICE_KEY:
        logger.error("MARKETPLACE_SERVICE_KEY is not set; Marketplace will refuse every stock call.")
        return FAULT
    try:
        status, body = _request("POST", f"/internal/allocations/{ref}/{action}",
                                {"listing_id": int(listing_id)}, internal=True)
    except MarketplaceUnavailable:
        return UNKNOWN
    if status == 200 and _held(body, ref) and body.get("status") in _FINISHED_BY[action]:
        return DONE
    logger.error("Unexpected %s answer %s (%s) for %s", action, status,
                 body.get("code", "?") if isinstance(body, dict) else "?", ref)
    return FAULT


def demo_rewind(since, refs):
    """Demo clock only: Marketplace deletes the allocations made after `since`
    and those of the orders being deleted. Returns how many it removed; raises
    MarketplaceUnavailable unless Marketplace answered 200."""
    if not settings.MARKETPLACE_SERVICE_KEY:
        raise MarketplaceUnavailable("Marketplace service key is not configured")
    status, body = _request("POST", "/internal/demo-rewind",
                            {"since": since.isoformat(), "refs": [str(ref) for ref in refs]}, internal=True)
    if status != 200:
        raise MarketplaceUnavailable(f"Marketplace answered {status}")
    return body.get("allocations_removed", 0) if isinstance(body, dict) else 0


def allocate(ref, listing_id, quantity, expected_terms_revision):
    if not settings.MARKETPLACE_SERVICE_KEY:
        logger.error("MARKETPLACE_SERVICE_KEY is not set; Marketplace will refuse every stock call.")
        return Answer(FAULT, code="no_service_key")
    try:
        status, body = _request("POST", "/internal/allocations", {
            "reservation_ref": str(ref),
            "listing_id": int(listing_id),
            "quantity": int(quantity),
            "expected_terms_revision": int(expected_terms_revision),
        }, internal=True)
    except MarketplaceUnavailable:
        return Answer(UNKNOWN)

    if status in (200, 201) and _held(body, ref):
        # A repeated call may find the hold already released (a tombstone).
        return Answer(body.get("status") if body.get("status") in (HELD, RELEASED) else FAULT, body)
    if status == 409 and isinstance(body, dict) and _held(body.get("allocation"), ref) \
            and body["allocation"].get("status") == REJECTED:
        return Answer(REJECTED, body["allocation"], body["allocation"].get("reject_reason") or "")
    logger.error("Unexpected allocate answer %s (%s) for %s", status, (body or {}).get("code", "?")
                 if isinstance(body, dict) else "?", ref)
    return Answer(FAULT, code=(body or {}).get("code", "") if isinstance(body, dict) else "")


def release(ref, listing_id):
    """True only when Marketplace confirmed the release (200)."""
    if not settings.MARKETPLACE_SERVICE_KEY:
        logger.error("MARKETPLACE_SERVICE_KEY is not set; Marketplace will refuse every stock call.")
        return False
    try:
        status, body = _request("POST", f"/internal/allocations/{ref}/release",
                                {"listing_id": int(listing_id)}, internal=True)
    except MarketplaceUnavailable:
        return False
    if status == 200 and _held(body, ref):
        return True
    logger.error("Unexpected release answer %s for %s", status, ref)
    return False


def shop_cancel(ref, listing_id, actor_user_id, reason):
    """LP-79: the restaurant cancelled, so the held units leave stock instead
    of going back on sale. True only when Marketplace confirmed it (200 with
    status SHOP_CANCELLED); Marketplace answers a repeat the same way and
    never adjusts stock twice."""
    if not settings.MARKETPLACE_SERVICE_KEY:
        logger.error("MARKETPLACE_SERVICE_KEY is not set; Marketplace will refuse every stock call.")
        return False
    try:
        status, body = _request("POST", f"/internal/allocations/{ref}/shop-cancel", {
            "listing_id": int(listing_id),
            "actor_user_id": int(actor_user_id),
            "reason": str(reason),
        }, internal=True)
    except MarketplaceUnavailable:
        return False
    if status == 200 and _held(body, ref) and body.get("status") == "SHOP_CANCELLED":
        return True
    logger.error("Unexpected shop-cancel answer %s for %s", status, ref)
    return False
