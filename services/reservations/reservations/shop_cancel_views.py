"""LP-79: the restaurant cancels orders, with a reason.

POST /api/shop/reservations/{ref}/cancel   (operator token)
    {"reason": "Food ran out" | "Closing early" | "Other"}
    200 the Cancelled order; 400 reason_required; 404 not one of this shop's
    orders; 409 already_cancelled / already_paid / expired /
    pickup_time_passed; 503 Marketplace could not say which shop is yours.
POST /api/shop/reservations/cancel-all     (operator token)
    {"branch_id": 7, "reason": "Closing early"}
    200 {"cancelled": n, "orders": [...]}: every Reserved order of that
    branch whose pick-up-by is later today; 400 / 404 / 503 as above.
GET  /api/admin/shop-cancellations[?shop_id=3]   (admin token, for LP-43)
    200 {"shops": [{"shop_id": 3, "shop_cancellations": 2}, ...]}

The shop is always the operator's own, looked up in Marketplace from the
token's user ID; a shop or branch sent by the browser is never trusted.
"""

from django.views.decorators.cache import never_cache
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .authentication import IsAdmin, IsOperator
from .cancellation import CancelRefused, cancel_all_for_today, cancel_by_shop, shop_cancellation_counts
from .collection import CollectionRefused
from .collection_views import owned_shop, shop_order_data


def _body(request):
    return request.data if isinstance(request.data, dict) else {}


@never_cache
@api_view(["POST"])
@permission_classes([IsAuthenticated, IsOperator])
def shop_cancel(request, ref):
    try:
        shop = owned_shop(request.user.id)
        order = cancel_by_shop(ref, shop, request.user.id, _body(request).get("reason"))
    except CollectionRefused as error:
        return Response(error.body, status=error.status)
    except CancelRefused as refused:
        return Response(refused.body, status=refused.http_status)
    return Response(shop_order_data(order))


@never_cache
@api_view(["POST"])
@permission_classes([IsAuthenticated, IsOperator])
def shop_cancel_all(request):
    data = _body(request)
    try:
        shop = owned_shop(request.user.id)
        orders = cancel_all_for_today(shop, data.get("branch_id"), request.user.id, data.get("reason"))
    except CollectionRefused as error:
        return Response(error.body, status=error.status)
    except CancelRefused as refused:
        return Response(refused.body, status=refused.http_status)
    return Response({"cancelled": len(orders), "orders": [shop_order_data(order) for order in orders]})


@never_cache
@api_view(["GET"])
@permission_classes([IsAuthenticated, IsAdmin])
def shop_cancellations(request):
    shop_id = request.query_params.get("shop_id")
    if shop_id is not None:
        if not shop_id.isdigit():
            return Response({"code": "invalid_request", "detail": "shop_id must be a number."}, status=400)
        shop_id = int(shop_id)
        counts = shop_cancellation_counts(shop_id)
        counts.setdefault(shop_id, 0)
    else:
        counts = shop_cancellation_counts()
    return Response({"shops": [{"shop_id": shop, "shop_cancellations": count} for shop, count in counts.items()]})
