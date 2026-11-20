"""LP-41: the customer's bell and notifications page.

Notifications live in Reservations (it owns the orders they are about). The
web only shows them: the bell asks for the unread count, and opening the page
lists them and then marks the ones shown as read.
"""

from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from . import services
from .account_views import authenticated_call, customer_required
from .browse_views import lebanon_time
from .order_views import _clock
from .services import ServiceUnavailable

UNAVAILABLE = "Can't load your notifications right now, please try again."


def for_display(item):
    created = lebanon_time(item.get("created_at"))
    return {
        "message": item.get("message") or "",
        "when": _clock(created),
        "unread": item.get("read") is False,
        "order_ref": item.get("reservation_ref") or "",
        "ref": item.get("ref") or "",
    }


@customer_required
@require_GET
def notifications_page(request):
    template = "frontend/notifications.html"
    try:
        status, data = authenticated_call(request, services.notifications)
    except ServiceUnavailable:
        return render(request, template, {"unavailable": UNAVAILABLE}, status=503)
    if status != 200 or not isinstance(data, dict) or not isinstance(data.get("notifications"), list):
        return render(request, template, {"unavailable": UNAVAILABLE}, status=503)

    items = [for_display(n) for n in data["notifications"] if isinstance(n, dict)]
    response = render(request, template, {"notifications": items})
    # They are shown (unread ones highlighted), so they count as read from now.
    unread = [item["ref"] for item in items if item["unread"] and item["ref"]]
    if unread:
        try:
            authenticated_call(request, services.mark_notifications_read, unread)
        except Exception:  # noqa: BLE001 - the page is already built; they stay unread and show again
            pass
    return response


@customer_required
@require_GET
def notifications_count(request):
    try:
        status, data = authenticated_call(request, services.notifications, True)
    except ServiceUnavailable:
        return JsonResponse({"detail": "Notifications unavailable"}, status=503)
    unread = data.get("unread") if status == 200 and isinstance(data, dict) else None
    if not isinstance(unread, int) or unread < 0:
        return JsonResponse({"detail": "Notifications unavailable"}, status=503)
    return JsonResponse({"unread": unread})
