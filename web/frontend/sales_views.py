"""LP-33: thin operator reporting pages; calculations stay in Reservations."""
from urllib.parse import urlencode

from django.http import HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_GET

from . import services
from .account_views import authenticated_call, operator_required
from .shop_order_views import owner_orders_gate


@operator_required
@require_GET
def history(request, export=False):
    gate = owner_orders_gate(request)
    if gate is not None:
        return gate
    filters = {key: request.GET.get(key, "") for key in ("from", "to", "branch")}
    if not export:
        filters["page"] = request.GET.get("page", "1")
    try:
        status, data = authenticated_call(request, services.export_sales if export else services.shop_sales, filters)
    except services.ServiceUnavailable:
        status, data = 503, None
    if export and status == 200 and isinstance(data, bytes):
        response = HttpResponse(data, content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="lastplate-sales.csv"'
        return response
    context = {"filters": filters, "unavailable": True}
    if not export and status == 200 and isinstance(data, dict):
        try:
            if not isinstance(data["summary"], dict) or not isinstance(data["orders"], list) \
                    or not isinstance(data["branches"], list) or not isinstance(data["filters"], dict):
                raise ValueError()
            pagination = data["pagination"]
            page, size, total = (pagination[k] for k in ("page", "page_size", "total"))
            if any(type(v) is not int for v in (page, size, total)) or page < 1 or size < 1 or total < 0:
                raise ValueError()
            filters = {k: data["filters"].get(k) or "" for k in ("from", "to", "branch")}
            context.update(data, filters=filters, unavailable=False,
                           export_url=reverse("sales-export") + "?" + urlencode(filters))
            for label, number in (("previous_url", page - 1), ("next_url", page + 1)):
                if number >= 1 and (number < page or page * size < total):
                    context[label] = "?" + urlencode({**filters, "page": number})
        except (KeyError, TypeError, ValueError):
            status = 503
    if context["unavailable"]:
        status = status if status in (400, 403, 404) else 503
        context["error"] = data.get("detail") if isinstance(data, dict) else None
    return render(request, "frontend/sales_history.html", context, status=status)
