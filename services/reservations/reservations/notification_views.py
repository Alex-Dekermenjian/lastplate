"""LP-41: the customer's notifications.

GET  /api/notifications            (customer token)
    {"unread": 2, "notifications": [...]}, newest first, at most 50.
    ?count_only=1 answers just {"unread": 2} (for the bell).
POST /api/notifications/read       (customer token)
    {"refs": ["<ref>", ...]} marks those read; no "refs" marks all of them.
    Answers {"marked": 1, "unread": 1}.

Only the signed-in customer's own notifications are ever read or changed: a
reference that belongs to someone else is simply ignored, so it reveals nothing.
"""

import uuid

from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .authentication import IsCustomer
from .models import Notification
from .timers import expire_due_orders_quietly

MAX_LISTED = 50


def notification_data(notification):
    return {
        "ref": str(notification.ref),
        "kind": notification.kind,
        "message": notification.message,
        "created_at": notification.created_at.isoformat(),
        "read": notification.read_at is not None,
        "reservation_ref": str(notification.order.reservation_ref),
    }


def mine(request):
    return Notification.objects.filter(customer_user_id=request.user.id)


def unread_count(request):
    return mine(request).filter(read_at__isnull=True).count()


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsCustomer])
def notifications(request):
    # LP-25 / LP-41: late orders expire and reminders are sent before reading.
    expire_due_orders_quietly()
    if request.query_params.get("count_only"):
        return Response({"unread": unread_count(request)})
    listed = mine(request).select_related("order")[:MAX_LISTED]
    return Response({"unread": unread_count(request), "notifications": [notification_data(n) for n in listed]})


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsCustomer])
def mark_read(request):
    data = request.data if isinstance(request.data, dict) else {}
    selected = mine(request).filter(read_at__isnull=True)
    if "refs" in data:
        refs = data["refs"]
        if not isinstance(refs, list):
            return Response({"code": "invalid_request", "detail": "refs must be a list."}, status=400)
        try:
            selected = selected.filter(ref__in=[uuid.UUID(str(ref)) for ref in refs])
        except ValueError:
            return Response({"code": "invalid_request", "detail": "refs must be notification references."},
                            status=400)
    marked = selected.update(read_at=timezone.now())
    return Response({"marked": marked, "unread": unread_count(request)})
