"""LP-19: the customer places an order from the cart.

POST /api/reservations  (customer access token)
    {"request_key": "<uuid>", "lines": [{"listing_id": 42, "quantity": 2,
                                        "expected_unit_price": "1.50"}]}

Answers:
- 201 a new Reserved order; 200 the same order again (same request_key);
- 202 {"code": "try_again"}: Marketplace gave no answer. Send the same
  request_key again to continue the same order;
- 409 {"code": "cart_changed", "problems": [...]}: nothing was reserved;
- 409 too_many_orders / request_key_reused / not_reserved, 403 customer_blocked
  or not a customer, 400 invalid_request, 503 when the cart could not even be
  checked (nothing saved).
"""

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .authentication import IsCustomer
from .checkout import CheckoutRefused, TryAgain, place_order


def order_data(order):
    return {
        "reservation_ref": str(order.reservation_ref),
        "status": order.status,
        "status_label": order.status_label,
        "pickup_code": order.pickup_code,
        "pick_up_by": order.pick_up_by.isoformat(),
        "created_at": order.created_at.isoformat(),
        "total_cash": str(order.total_cash),
        "shop_id": order.shop_id,
        "branch_id": order.branch_id or None,
        "lines": [
            {
                "listing_id": line.listing_id,
                "title": line.title,
                "quantity": line.quantity,
                "unit_price": str(line.unit_price),
                "pickup_start": line.pickup_start.isoformat(),
                "pickup_end": line.pickup_end.isoformat(),
            }
            for line in order.lines.all()
        ],
    }


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsCustomer])
def reservations(request):
    data = request.data if isinstance(request.data, dict) else {}
    try:
        result = place_order(request.user.id, data.get("request_key"), data.get("lines"))
    except CheckoutRefused as refused:
        body = {"code": refused.code, "detail": refused.detail}
        if refused.problems:
            body["problems"] = refused.problems
        return Response(body, status=refused.http_status)
    except TryAgain as again:
        # Saved but not confirmed (202), or not saved at all (503).
        return Response({"code": "try_again", "detail": again.detail},
                        status=status.HTTP_202_ACCEPTED if again.order else status.HTTP_503_SERVICE_UNAVAILABLE)

    return Response(order_data(result.order),
                    status=status.HTTP_201_CREATED if result.created else status.HTTP_200_OK)
