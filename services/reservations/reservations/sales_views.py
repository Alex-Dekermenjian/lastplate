"""Operator sales JSON and CSV share ownership, filters and calculations."""
import csv

from django.http import HttpResponse
from django.views.decorators.cache import never_cache
from rest_framework.decorators import api_view, permission_classes, renderer_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response

from .authentication import IsOperator
from .sales import ReportRefused, owned_shop, report


class CSVRenderer(JSONRenderer):
    # CSV success is an HttpResponse. Errors remain JSON, including when the
    # download client asks for text/csv, so token refresh can handle a 401.
    media_type = "text/csv"
    format = "csv"

    def render(self, data, accepted_media_type=None, renderer_context=None):
        if renderer_context:
            renderer_context["response"]["Content-Type"] = "application/json"
        return super().render(data, accepted_media_type, renderer_context)


def csv_cell(value):
    text = "" if value is None else str(value)
    if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")):
        return "'" + text
    return text


@never_cache
@api_view(["GET"])
@renderer_classes([JSONRenderer, CSVRenderer])
@permission_classes([IsAuthenticated, IsOperator])
def sales(request, export=False):
    try:
        data = report(owned_shop(request.user.id), request.query_params, export=export)
    except ReportRefused as refused:
        return Response(refused.body, status=refused.status)
    if not export:
        return Response(data)
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="lastplate-sales.csv"'
    columns = ("reservation_ref", "branch_id", "branch_name", "status_label", "pick_up_by", "collected_at",
               "quantity", "order_total", "cash_collected", "cash_expected", "items_saved")
    writer = csv.writer(response)
    writer.writerow((*columns, "currency"))
    for row in data["orders"]:
        writer.writerow([csv_cell(row[key]) for key in columns] + ["USD"])
    return response
