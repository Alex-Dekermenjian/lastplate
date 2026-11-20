"""POST /api/demo/rewind: the web site moved the demo clock back.

Does not exist (404) unless the demo clock is on (DEMO_CLOCK_FILE set), so it
is never reachable in CI or production. Like /api/timers/expire it needs no
sign-in: the web site calls it for whoever moves the clock, and the demo clock
itself lets everyone move it.
"""

from django.http import Http404
from django.utils.dateparse import parse_datetime
from django.utils import timezone
from rest_framework.decorators import api_view, authentication_classes, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from config import demo_clock

from . import demo_rewind
from .marketplace_client import MarketplaceUnavailable


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([])
def rewind(request):
    if not demo_clock.enabled():
        raise Http404
    raw = request.data.get("since") if isinstance(request.data, dict) else None
    since = parse_datetime(raw) if isinstance(raw, str) else None
    if since is None or timezone.is_naive(since):
        return Response({"code": "invalid_request", "detail": "since must be a date and time with a time zone."},
                        status=400)
    try:
        orders, allocations = demo_rewind.rewind(since)
    except MarketplaceUnavailable:
        return Response({"code": "marketplace_unavailable",
                         "detail": "Marketplace could not put the food back on sale, so nothing was deleted."},
                        status=503)
    return Response({"orders_removed": orders, "allocations_removed": allocations})
