"""LP-20: internal stock API, for the Reservations service only.

POST /internal/allocations                      hold stock (or store why not)
GET  /internal/allocations/{ref}                read the stored answer
POST /internal/allocations/{ref}/release        put held units back
POST /internal/allocations/{ref}/consume        held units were sold (paid)
POST /internal/allocations/{ref}/shop-cancel    restaurant cancels; units leave stock

Answers (docs/microservices-plan.md §6):
- 201 new HELD, 200 the same answer again for a repeated call;
- 409 with "allocation" = the stored REJECTED allocation: definitive refusal;
- any other 4xx (400, 401, 403, 404, 409 ref_conflict / not_held): nothing
  changed, and Reservations treats it as a fault, not as a refusal.
"""

import uuid

from rest_framework import status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .allocation_service import AllocationError, consume, release, reserve, shop_cancel
from .models import Allocation
from .service_auth import internal_endpoint

ONLY_RESERVATIONS = ("reservations",)
# More than any one order can need, and far below PositiveIntegerField's limit.
MAX_QUANTITY = 1000


def _error(code, detail, http_status, **extra):
    return Response({"code": code, "detail": detail, **extra}, status=http_status)


def _iso(moment):
    return moment.isoformat() if moment else None


def allocation_data(allocation):
    return {
        "reservation_ref": str(allocation.reservation_ref),
        "listing_id": allocation.listing_id,
        "quantity": allocation.quantity,
        "status": allocation.status,
        "reject_reason": allocation.reject_reason or None,
        "terms": {
            "title": allocation.title,
            "unit_price": None if allocation.unit_price is None else str(allocation.unit_price),
            "pickup_start": _iso(allocation.pickup_start),
            "pickup_end": _iso(allocation.pickup_end),
            "terms_revision": allocation.terms_revision,
            "shop_id": allocation.shop_id,
            "branch_id": allocation.branch_id,
        },
        "created_at": _iso(allocation.created_at),
        "finished_at": _iso(allocation.finished_at),
    }


class BadRequest(Exception):
    def __init__(self, detail):
        super().__init__(detail)
        self.detail = detail


def _body(request):
    if not isinstance(request.data, dict):
        raise BadRequest("Send a JSON object.")
    return request.data


def _positive_int(data, name, maximum=None):
    value = data.get(name)
    # bool is an int in Python; "true" is never a valid ID or quantity.
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise BadRequest(f"{name} must be a whole number of at least 1.")
    if maximum is not None and value > maximum:
        raise BadRequest(f"{name} must be at most {maximum}.")
    return value


def _uuid(value, name="reservation_ref"):
    try:
        return uuid.UUID(str(value)) if value is not None else None
    except ValueError:
        pass
    raise BadRequest(f"{name} must be a UUID.")


def _run(action):
    """Run a service call and turn its result or error into a response."""
    try:
        return action()
    except BadRequest as error:
        return _error("invalid_request", error.detail, status.HTTP_400_BAD_REQUEST)
    except AllocationError as error:
        return _error(error.code, error.detail, error.http_status)


def _changed_response(allocation, changed):
    return Response({**allocation_data(allocation), "changed": changed}, status=status.HTTP_200_OK)


def internal(methods):
    """api_view without user authentication: only the service key counts."""

    def decorator(view):
        view = internal_endpoint(ONLY_RESERVATIONS)(view)
        view = permission_classes([AllowAny])(view)
        view = authentication_classes([])(view)
        return api_view(methods)(view)

    return decorator


@internal(["POST"])
def allocations(request):
    def action():
        data = _body(request)
        ref = _uuid(data.get("reservation_ref"))
        if ref is None:
            raise BadRequest("reservation_ref is required.")
        allocation, created = reserve(
            reservation_ref=ref,
            listing_id=_positive_int(data, "listing_id"),
            quantity=_positive_int(data, "quantity", maximum=MAX_QUANTITY),
            expected_terms_revision=_positive_int(data, "expected_terms_revision"),
        )
        if allocation.status == Allocation.Status.REJECTED:
            return _error(
                allocation.reject_reason,
                allocation.get_reject_reason_display(),
                status.HTTP_409_CONFLICT,
                allocation=allocation_data(allocation),
            )
        return Response(
            allocation_data(allocation),
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    return _run(action)


@internal(["GET"])
def allocation_detail(request, ref):
    allocation = Allocation.objects.filter(reservation_ref=ref).first()
    if allocation is None:
        # Not a refusal: the reserve call may still be on its way (§6 case 2).
        return _error("allocation_not_found", "No allocation with this reference yet.", status.HTTP_404_NOT_FOUND)
    return Response(allocation_data(allocation))


@internal(["POST"])
def allocation_release(request, ref):
    return _run(lambda: _changed_response(*release(ref, _positive_int(_body(request), "listing_id"))))


@internal(["POST"])
def allocation_consume(request, ref):
    return _run(lambda: _changed_response(*consume(ref, _positive_int(_body(request), "listing_id"))))


@internal(["POST"])
def allocation_shop_cancel(request, ref):
    def action():
        data = _body(request)
        reason = data.get("reason")
        if not isinstance(reason, str):
            raise BadRequest("reason must be text.")
        return _changed_response(*shop_cancel(
            ref,
            _positive_int(data, "listing_id"),
            actor_user_id=_positive_int(data, "actor_user_id"),
            reason=reason,
        ))

    return _run(action)
