"""LP-28 and LP-19: /internal/allocations, for Reservations only.

POST /internal/allocations                  {"lines": [{reservation_ref, listing_id, quantity}, ...]}
    201 (new) or 200 (repeat): every line HELD, with the terms to snapshot.
    409 with "status": "REJECTED": nothing held; each line says why.
POST /internal/allocations/<ref>/release    {"listing_id": ...}
"""

from rest_framework import serializers, status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.response import Response

from .allocation_service import AllocationConflict, allocate_lines, release
from .internal_auth import ServiceKeyAuthentication, only_service
from .models import Allocation, Listing, Shop
from .serializers import RejectUnknownFieldsMixin

INTERNAL_AUTH = [ServiceKeyAuthentication]
RESERVATIONS_ONLY = [only_service("reservations")]
MAX_LINES = 50


class AllocationLineSerializer(RejectUnknownFieldsMixin, serializers.Serializer):
    reservation_ref = serializers.CharField(max_length=64)
    listing_id = serializers.IntegerField(min_value=1)
    quantity = serializers.IntegerField(min_value=1, max_value=1000)


class AllocateSerializer(RejectUnknownFieldsMixin, serializers.Serializer):
    lines = AllocationLineSerializer(many=True, allow_empty=False, max_length=MAX_LINES)


class ReleaseSerializer(RejectUnknownFieldsMixin, serializers.Serializer):
    listing_id = serializers.IntegerField(min_value=1)


def line_body(allocation):
    listing = allocation.listing
    return {
        "reservation_ref": allocation.reservation_ref,
        "listing_id": listing.pk,
        "quantity": allocation.quantity,
        "status": allocation.status,
        "reject_reason": allocation.reject_reason,
        # The agreed terms Reservations snapshots on the order line.
        "title": listing.title,
        "unit_price": str(listing.discounted_price) if listing.discounted_price is not None else None,
        "pickup_start": listing.pickup_start,
        "pickup_end": listing.pickup_end,
    }


def order_body(allocations):
    shop = Shop.objects.filter(pk=allocations[0].listing.shop_id).first()
    statuses = {a.status for a in allocations}
    if statuses == {Allocation.Status.HELD}:
        summary = "HELD"
    elif Allocation.Status.REJECTED in statuses:
        summary = "REJECTED"
    else:
        # For example a late request for lines already released: nothing held.
        summary = "RELEASED"
    return {
        "status": summary,
        "shop": {
            "id": shop.pk,
            "name": shop.name,
            "address": shop.address,
            "operator_user_id": shop.operator_user_id,
        } if shop else None,
        "lines": [line_body(a) for a in allocations],
    }


def _error(code, detail, http_status):
    return Response({"code": code, "detail": detail}, status=http_status)


@api_view(["POST"])
@authentication_classes(INTERNAL_AUTH)
@permission_classes(RESERVATIONS_ONLY)
def create_allocation(request):
    serializer = AllocateSerializer(data=request.data)
    if not serializer.is_valid():
        return Response({"code": "invalid", "fields": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)
    try:
        allocations, created = allocate_lines(serializer.validated_data["lines"])
    except Listing.DoesNotExist:
        return _error("not_found", "Listing not found.", status.HTTP_404_NOT_FOUND)
    except AllocationConflict as exc:
        return _error("conflict", str(exc), status.HTTP_409_CONFLICT)

    body = order_body(allocations)
    if body["status"] != "HELD":
        return Response(body, status=status.HTTP_409_CONFLICT)
    return Response(body, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


@api_view(["POST"])
@authentication_classes(INTERNAL_AUTH)
@permission_classes(RESERVATIONS_ONLY)
def release_allocation(request, ref):
    serializer = ReleaseSerializer(data=request.data)
    if not serializer.is_valid():
        return Response({"code": "invalid", "fields": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)
    try:
        allocation = release(ref, serializer.validated_data["listing_id"])
    except Listing.DoesNotExist:
        return _error("not_found", "Listing not found.", status.HTTP_404_NOT_FOUND)
    except AllocationConflict as exc:
        return _error("conflict", str(exc), status.HTTP_409_CONFLICT)
    return Response(line_body(allocation), status=status.HTTP_200_OK)
