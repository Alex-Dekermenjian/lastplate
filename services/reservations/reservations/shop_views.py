"""LP-42: shop ownership and today's order selection belong to Reservations."""
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.db.models import Q
from django.utils import timezone
from django.views.decorators.cache import never_cache
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .authentication import IsOperator
from .marketplace_client import MarketplaceUnavailable, operator_shop
from .models import Order, Status
from .views import order_data


@never_cache
@api_view(["GET"])
@permission_classes([IsAuthenticated, IsOperator])
def shop_reservations(request):
    try:
        status, shop = operator_shop(request.user.id)
        if status == 404:
            return Response({"code": "not_found", "detail": "No shop found."}, status=404)
        if status != 200 or not isinstance(shop, dict) or type(shop.get("id")) is not int:
            raise MarketplaceUnavailable()
        branches = shop["branches"]
        if not isinstance(branches, list) or any(
            not isinstance(b, dict) or type(b.get("id")) is not int or not isinstance(b.get("name"), str)
            for b in branches
        ):
            raise MarketplaceUnavailable()
    except (MarketplaceUnavailable, KeyError):
        return Response({"code": "unavailable", "detail": "Can't load orders right now. Please try again."}, status=503)

    now = timezone.now()
    zone = ZoneInfo("Asia/Beirut")
    today = now.astimezone(zone).date()
    start = datetime.combine(today, time.min, tzinfo=zone)
    end = datetime.combine(today + timedelta(days=1), time.min, tzinfo=zone)
    ids = [b["id"] for b in branches]
    orders = Order.objects.filter(shop_id=shop["id"], branch_id__in=ids,
                                  status=Status.CONFIRMED, pick_up_by__gte=start, pick_up_by__lt=end)
    count = orders.filter(pick_up_by__gt=now).count()
    branch = request.query_params.get("branch_id", "")
    if branch:
        try:
            branch_id = int(branch)
        except ValueError:
            branch_id = None
        if branch_id not in ids:
            return Response({"code": "not_found", "detail": "No such branch."}, status=404)
        orders = orders.filter(branch_id=branch_id)
    query = request.query_params.get("q", "").strip()[:150]
    if query:
        orders = orders.filter(Q(pickup_code__icontains=query) | Q(customer_first_name__icontains=query))
    rows = []
    if request.query_params.get("count_only") != "1":
        for order in orders.prefetch_related("lines").order_by("pick_up_by", "id"):
            rows.append({**order_data(order), "customer_first_name": order.customer_first_name or "Customer"})
    return Response({"orders": rows, "branches": branches, "count": count})
