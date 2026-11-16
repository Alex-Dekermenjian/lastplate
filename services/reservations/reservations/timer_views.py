"""LP-25: POST /api/timers/expire

The web calls this whenever it shows the offers list (home, browse, an offer,
a restaurant page), so late orders expire and their food is back on sale even
when the host has put the background timer to sleep.

No sign-in: it only does what the timer would do anyway (expire orders whose
pick-up time has passed), reveals nothing and is idempotent. To keep it cheap
it runs at most once every EXPIRE_EVERY_SECONDS per process; other calls in
that time answer {"ran": false}.
"""

from django.core.cache import cache
from rest_framework.decorators import api_view, authentication_classes, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .timers import expire_due_orders_quietly

EXPIRE_EVERY_SECONDS = 10
_CACHE_KEY = "lp25-expire-ran"


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([])
def expire(request):
    if not cache.add(_CACHE_KEY, True, timeout=EXPIRE_EVERY_SECONDS):
        return Response({"ran": False, "expired": 0})
    return Response({"ran": True, "expired": expire_due_orders_quietly()})
