"""LP-28: the restaurant's orders API.

GET  /api/shop/orders?status=PLACED         this restaurant's orders
POST /api/shop/orders/<ref>/accept
POST /api/shop/orders/<ref>/reject          {"reason": "..."}

Only the owning restaurant account may see or decide an order; anyone else
gets 404 for it, and non-restaurant accounts get 403.
"""

from rest_framework import serializers, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .authentication import IsShopOperator
from .models import Order, OrderLine
from .order_service import DecisionRefused, OrderNotFound, decide

RESTAURANT_ONLY = [IsAuthenticated, IsShopOperator]


class OrderLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderLine
        fields = ["listing_id", "title", "unit_price", "quantity", "pickup_start", "pickup_end"]
        read_only_fields = fields


class ShopOrderSerializer(serializers.ModelSerializer):
    lines = OrderLineSerializer(many=True, read_only=True)

    class Meta:
        model = Order
        # Never the pickup code: the customer shows it and the restaurant types it in (LP-24).
        fields = ["ref", "customer_name", "status", "payment_status", "total", "rejection_reason",
                  "created_at", "decided_at", "lines"]
        read_only_fields = fields


class RejectSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=500, allow_blank=True)


@api_view(["GET"])
@permission_classes(RESTAURANT_ONLY)
def shop_orders(request):
    wanted = request.query_params.get("status", Order.Status.PLACED).upper()
    if wanted not in Order.Status.values:
        return Response({"code": "invalid", "detail": "Unknown status."}, status=status.HTTP_400_BAD_REQUEST)
    orders = (
        Order.objects.filter(shop_operator_user_id=request.user.id, status=wanted)
        .prefetch_related("lines")
        .order_by("created_at", "id")
    )
    return Response(ShopOrderSerializer(orders, many=True).data)


def _decide(request, ref, decision, reason=""):
    try:
        order = decide(ref, request.user.id, decision, reason)
    except OrderNotFound:
        return Response({"code": "not_found", "detail": "Order not found."}, status=status.HTTP_404_NOT_FOUND)
    except DecisionRefused as refused:
        http = status.HTTP_400_BAD_REQUEST if refused.code == "reason_required" else status.HTTP_409_CONFLICT
        return Response({"code": refused.code, "detail": refused.detail}, status=http)
    return Response(ShopOrderSerializer(order).data)


@api_view(["POST"])
@permission_classes(RESTAURANT_ONLY)
def accept_order(request, ref):
    return _decide(request, ref, Order.Status.ACCEPTED)


@api_view(["POST"])
@permission_classes(RESTAURANT_ONLY)
def reject_order(request, ref):
    serializer = RejectSerializer(data=request.data)
    if not serializer.is_valid():
        return Response({"code": "invalid", "fields": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)
    return _decide(request, ref, Order.Status.REJECTED, serializer.validated_data["reason"])
